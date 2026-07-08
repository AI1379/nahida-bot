"""Config schema introspection service.

Extracted from CLI config_commands so both CLI and WebUI can share it.
"""

from __future__ import annotations

import importlib.util
import json
import types as _types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

from pydantic_core import PydanticUndefined
from pydantic.fields import FieldInfo

from nahida_bot.core.config import (
    AgentConfig,
    ContextConfig,
    KBAutoRecallConfig,
    MultimodalConfig,
    ProviderEntryConfig,
    RouterConfigModel,
    SchedulerConfigModel,
    Settings,
    load_settings,
)

# -- Data models ---------------------------------------------------------------


@dataclass(slots=True)
class SchemaEntry:
    path: str
    type_: str
    default_: str
    constraints: str = "-"


# -- Type helpers --------------------------------------------------------------


_SIMPLE_TYPES: dict[type, str] = {
    bool: "bool",
    str: "str",
    int: "int",
    float: "float",
    list: "list",
    dict: "dict",
    type(None): "null",
}


def human_type(annotation: Any) -> str:
    """Return a short human-readable type name for an annotation."""
    if annotation is None:
        return "any"
    if isinstance(annotation, type) and annotation in _SIMPLE_TYPES:
        return _SIMPLE_TYPES[annotation]

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is None and isinstance(annotation, _types.UnionType):
        return " | ".join(human_type(a) for a in args)

    if origin is not None:
        inner = ", ".join(human_type(a) for a in args)
        if origin is dict:
            return f"dict[{inner}]"
        if origin is list:
            return f"list[{inner}]"
        if origin is Literal:
            return " | ".join(repr(a) for a in args)
        if origin is _types.UnionType:
            return " | ".join(human_type(a) for a in args)
        origin_name = getattr(origin, "__name__", str(origin))
        return f"{origin_name}[{inner}]" if inner else origin_name

    if hasattr(annotation, "model_fields"):
        return annotation.__name__

    return str(annotation).replace("nahida_bot.core.config.", "")


def format_default(value: Any) -> str:
    """Format a default value for display."""
    if value is PydanticUndefined:
        return "required"
    if value is None:
        return "-"
    if isinstance(value, str) and value == "":
        return '""'
    if isinstance(value, list):
        dumped = json.dumps(value, ensure_ascii=False)
        return dumped if len(dumped) <= 40 else dumped[:37] + "..."
    if isinstance(value, dict):
        return "{}" if not value else "{...}"
    return str(value)


def field_default(field_info: FieldInfo) -> str:
    """Format a Pydantic field default, including default_factory values."""
    return format_default(field_info.get_default(call_default_factory=True))


def format_constraints(field_info: FieldInfo) -> str:
    """Format constraints from FieldInfo metadata."""
    parts: list[str] = []
    for entry in field_info.metadata:
        for name, symbol in [("gt", ">"), ("ge", ">="), ("lt", "<"), ("le", "<=")]:
            value = getattr(entry, name, None)
            if value is not None:
                parts.append(f"{symbol}{value}")

        constraints = getattr(entry, "constraints", None)
        if isinstance(constraints, dict):
            for name, symbol in [
                ("gt", ">"),
                ("ge", ">="),
                ("lt", "<"),
                ("le", "<="),
            ]:
                if name in constraints:
                    parts.append(f"{symbol}{constraints[name]}")
    return " ".join(parts) if parts else "-"


# -- Schema building -----------------------------------------------------------


def walk_schema(model_cls: type, prefix: str = "") -> list[SchemaEntry]:
    """Recursively walk a pydantic model and return flat schema entries."""
    entries: list[SchemaEntry] = []
    for fname, finfo in model_cls.model_fields.items():
        path = f"{prefix}.{fname}" if prefix else fname
        annotation = finfo.annotation

        if hasattr(annotation, "model_fields"):
            entries.append(
                SchemaEntry(path=path, type_=annotation.__name__, default_="")
            )
            entries.extend(walk_schema(annotation, path))
            continue

        entries.append(
            SchemaEntry(
                path=path,
                type_=human_type(annotation),
                default_=field_default(finfo),
                constraints=format_constraints(finfo),
            )
        )
    return entries


# Top-level nested config models.
_NESTED_MODELS: dict[str, type] = {
    "multimodal": MultimodalConfig,
    "agent": AgentConfig,
    "context": ContextConfig,
    "scheduler": SchedulerConfigModel,
    "router": RouterConfigModel,
    "kb_auto_recall": KBAutoRecallConfig,
}


def _plugin_config_model(plugin_id: str) -> type | None:
    """Return a hand-authored config model for built-in plugins/channels."""
    if plugin_id == "telegram":
        from nahida_bot.channels.telegram.config import TelegramPluginConfig

        return TelegramPluginConfig
    if plugin_id == "milky":
        from nahida_bot.channels.milky.config import MilkyPluginConfig

        return MilkyPluginConfig
    if plugin_id == "onebot":
        from nahida_bot.channels.onebot.config import OneBotPluginConfig

        return OneBotPluginConfig
    if plugin_id == "conversation_joiner":
        from nahida_bot.plugins.conversation_joiner.config import (
            ConversationJoinerConfig,
        )

        return ConversationJoinerConfig
    if plugin_id == "knowledge_base":
        from nahida_bot.plugins.knowledge_base.config import KBConfig

        return KBConfig
    return None


def build_config_schema(
    section: str | None = None,
    show_providers: bool = False,
    *,
    show_plugins: bool = True,
    config_yaml: str | None = None,
) -> list[SchemaEntry]:
    """Build the schema entry list, optionally filtered by section."""
    entries: list[SchemaEntry] = []

    nested_keys = {*_NESTED_MODELS, "providers", "memory", "model_routing"}
    for fname, finfo in Settings.model_fields.items():
        if fname in nested_keys:
            continue
        entries.append(
            SchemaEntry(
                path=fname,
                type_=human_type(finfo.annotation),
                default_=field_default(finfo),
                constraints=format_constraints(finfo),
            )
        )

    entries.append(
        SchemaEntry(
            path="providers", type_="dict[str, ProviderEntryConfig]", default_="{}"
        )
    )
    if show_providers:
        for e in walk_schema(ProviderEntryConfig, "providers.<id>"):
            entries.append(e)

    for sub_path, model_cls in _NESTED_MODELS.items():
        if (
            section
            and not sub_path.startswith(section)
            and not (section.startswith(sub_path))
        ):
            continue
        for e in walk_schema(model_cls, sub_path):
            entries.append(e)

    if not section or section.startswith("memory"):
        mem_entries = _build_memory_schema()
        entries.extend(mem_entries)

    if show_plugins:
        entries.extend(_build_plugin_schema(section=section, config_yaml=config_yaml))

    if section:
        entries = [e for e in entries if e.path.startswith(section)]

    return entries


def _build_memory_schema() -> list[SchemaEntry]:
    """Walk the full MemoryConfig sub-tree."""
    from nahida_bot.core.config import MemoryConfig

    entries: list[SchemaEntry] = []
    mem = MemoryConfig()
    entries.append(
        SchemaEntry(path="memory.enabled", type_="bool", default_=str(mem.enabled))
    )

    for e in walk_schema(type(mem.retrieval), "memory.retrieval"):
        entries.append(e)
    for e in walk_schema(type(mem.embedding), "memory.embedding"):
        entries.append(e)
    for e in walk_schema(type(mem.consolidation), "memory.consolidation"):
        entries.append(e)
    return entries


def _build_plugin_schema(
    *, section: str | None = None, config_yaml: str | None = None
) -> list[SchemaEntry]:
    """Build schema entries for discovered plugin configuration sections."""
    try:
        settings = load_settings(config_yaml=config_yaml)
    except Exception:
        settings = Settings()

    entries: list[SchemaEntry] = []
    for manifest in _discover_plugin_manifests(settings):
        plugin_id = manifest.id
        if section and not (
            plugin_id.startswith(section) or section.startswith(plugin_id)
        ):
            continue

        entries.append(
            SchemaEntry(
                path=plugin_id,
                type_=f"PluginConfig ({manifest.name})",
                default_="{}",
            )
        )

        config_model = _plugin_config_model(plugin_id)
        if config_model is not None:
            entries.extend(walk_schema(config_model, plugin_id))
            continue

        config_schema = manifest.config_schema or {}
        if config_schema:
            entries.extend(
                _walk_json_schema(
                    config_schema,
                    prefix=plugin_id,
                    defaults=manifest.config,
                )
            )
        else:
            entries.extend(_infer_config_entries(manifest.config, prefix=plugin_id))
    return entries


def _discover_plugin_manifests(settings: Settings) -> list[Any]:
    """Discover plugin manifests using the same paths as Application startup."""
    from nahida_bot.plugins.loader import PluginLoader

    paths: list[Path] = []
    builtin_plugins = _package_dir("nahida_bot.plugins.builtin")
    if builtin_plugins is not None:
        paths.append(builtin_plugins)

    if settings.discover_builtin_channels:
        builtin_channels = _package_dir("nahida_bot.channels")
        if builtin_channels is not None:
            paths.append(builtin_channels)

    for module_name in (
        "nahida_bot.plugins.mcp",
        "nahida_bot.plugins.conversation_joiner",
        "nahida_bot.plugins.image_generation",
        "nahida_bot.plugins.knowledge_base",
        "nahida_bot.plugins.tts",
    ):
        plugin_dir = _package_dir(module_name)
        if plugin_dir is not None:
            paths.append(plugin_dir)

    paths.extend(
        path for p in settings.plugin_paths if (path := Path(p).resolve()).is_dir()
    )

    loader = PluginLoader()
    manifests: dict[str, Any] = {}
    for manifest, _plugin_dir in loader.discover(paths):
        manifests.setdefault(manifest.id, manifest)
    return list(manifests.values())


def _package_dir(module_name: str) -> Path | None:
    spec = importlib.util.find_spec(module_name)
    if spec is None:
        return None
    if spec.submodule_search_locations:
        return Path(next(iter(spec.submodule_search_locations)))
    if spec.origin:
        return Path(spec.origin).parent
    return None


def _walk_json_schema(
    schema: dict[str, Any],
    *,
    prefix: str,
    defaults: dict[str, Any],
) -> list[SchemaEntry]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return []

    required = schema.get("required", [])
    required_fields = set(required) if isinstance(required, list) else set()
    entries: list[SchemaEntry] = []
    for name, prop in properties.items():
        if not isinstance(prop, dict):
            continue
        path = f"{prefix}.{name}"
        default_value = defaults.get(name, prop.get("default", PydanticUndefined))
        constraints = _json_schema_constraints(prop)
        if name in required_fields and default_value is PydanticUndefined:
            default_text = "required"
        else:
            default_text = format_default(default_value)

        child_properties = prop.get("properties")
        if isinstance(child_properties, dict):
            entries.append(
                SchemaEntry(
                    path=path,
                    type_=_json_schema_type(prop),
                    default_=default_text,
                    constraints=constraints,
                )
            )
            child_defaults = defaults.get(name, {})
            entries.extend(
                _walk_json_schema(
                    prop,
                    prefix=path,
                    defaults=child_defaults if isinstance(child_defaults, dict) else {},
                )
            )
            continue

        entries.append(
            SchemaEntry(
                path=path,
                type_=_json_schema_type(prop),
                default_=default_text,
                constraints=constraints,
            )
        )
    return entries


def _infer_config_entries(config: dict[str, Any], *, prefix: str) -> list[SchemaEntry]:
    entries: list[SchemaEntry] = []
    for name, value in config.items():
        path = f"{prefix}.{name}"
        if isinstance(value, dict):
            entries.append(SchemaEntry(path=path, type_="dict", default_="{...}"))
            entries.extend(_infer_config_entries(value, prefix=path))
            continue
        entries.append(
            SchemaEntry(
                path=path,
                type_=_value_type(value),
                default_=format_default(value),
            )
        )
    return entries


def _json_schema_type(schema: dict[str, Any]) -> str:
    if "enum" in schema and isinstance(schema["enum"], list):
        return " | ".join(repr(value) for value in schema["enum"])
    for key in ("anyOf", "oneOf"):
        variants = schema.get(key)
        if isinstance(variants, list):
            return " | ".join(
                _json_schema_type(v) for v in variants if isinstance(v, dict)
            )
    raw_type = schema.get("type")
    if isinstance(raw_type, list):
        return " | ".join(str(item) for item in raw_type)
    if raw_type == "array":
        item_type = "any"
        items = schema.get("items")
        if isinstance(items, dict):
            item_type = _json_schema_type(items)
        return f"list[{item_type}]"
    if raw_type == "object":
        return "dict"
    if isinstance(raw_type, str):
        return {
            "boolean": "bool",
            "integer": "int",
            "number": "float",
            "string": "str",
            "null": "null",
        }.get(raw_type, raw_type)
    return "any"


def _json_schema_constraints(schema: dict[str, Any]) -> str:
    parts: list[str] = []
    for name, symbol in [
        ("exclusiveMinimum", ">"),
        ("minimum", ">="),
        ("exclusiveMaximum", "<"),
        ("maximum", "<="),
        ("minLength", "len>="),
        ("maxLength", "len<="),
        ("minItems", "items>="),
        ("maxItems", "items<="),
    ]:
        if name in schema:
            parts.append(f"{symbol}{schema[name]}")
    return " ".join(parts) if parts else "-"


def _value_type(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int) and not isinstance(value, bool):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        if not value:
            return "list"
        item_types = sorted({_value_type(item) for item in value})
        return f"list[{' | '.join(item_types)}]"
    if isinstance(value, dict):
        return "dict"
    if value is None:
        return "null"
    return type(value).__name__
