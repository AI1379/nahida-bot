"""Configuration management subcommands: schema and validate."""

from __future__ import annotations

import json
import types as _types
from dataclasses import dataclass, field
from typing import Any, Literal, get_args, get_origin

import typer
from pydantic_core import PydanticUndefined
from pydantic.fields import FieldInfo
from rich.console import Console
from rich.table import Table
from rich.text import Text

from nahida_bot.core.config import (
    AgentConfig,
    ContextConfig,
    MultimodalConfig,
    ProviderEntryConfig,
    RouterConfigModel,
    SchedulerConfigModel,
    Settings,
    load_settings,
)

config_app = typer.Typer(help="Configuration management")
console = Console()

# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

_SIMPLE_TYPES: dict[type, str] = {
    bool: "bool",
    str: "str",
    int: "int",
    float: "float",
    list: "list",
    dict: "dict",
    type(None): "null",
}


def _human_type(annotation: Any) -> str:
    """Return a short human-readable type name for an annotation."""
    if annotation is None:
        return "any"
    if isinstance(annotation, type) and annotation in _SIMPLE_TYPES:
        return _SIMPLE_TYPES[annotation]

    origin = get_origin(annotation)
    args = get_args(annotation)

    # Python 3.10+ UnionType (e.g. int | None)
    if origin is None and isinstance(annotation, _types.UnionType):
        return " | ".join(_human_type(a) for a in args)

    if origin is not None:
        inner = ", ".join(_human_type(a) for a in args)
        if origin is dict:
            return f"dict[{inner}]"
        if origin is list:
            return f"list[{inner}]"
        if origin is Literal:
            return " | ".join(repr(a) for a in args)
        if origin is _types.UnionType:
            return " | ".join(_human_type(a) for a in args)
        origin_name = getattr(origin, "__name__", str(origin))
        return f"{origin_name}[{inner}]" if inner else origin_name

    if hasattr(annotation, "model_fields"):
        return annotation.__name__

    return str(annotation).replace("nahida_bot.core.config.", "")


def _format_default(value: Any) -> str:
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


def _field_default(field_info: FieldInfo) -> str:
    """Format a Pydantic field default, including default_factory values."""
    return _format_default(field_info.get_default(call_default_factory=True))


def _format_constraints(field_info: FieldInfo) -> str:
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


@dataclass(slots=True)
class SchemaEntry:
    path: str
    type_: str
    default_: str
    constraints: str = "-"


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
                type_=_human_type(annotation),
                default_=_field_default(finfo),
                constraints=_format_constraints(finfo),
            )
        )
    return entries


# Top-level nested config models (recurse into their children).
# These paths must match Settings field names.
_NESTED_MODELS: dict[str, type] = {
    "multimodal": MultimodalConfig,
    "agent": AgentConfig,
    "context": ContextConfig,
    "scheduler": SchedulerConfigModel,
    "router": RouterConfigModel,
}


# ---------------------------------------------------------------------------
# config schema
# ---------------------------------------------------------------------------


@config_app.command(name="schema")
def schema_cmd(
    section: str | None = typer.Option(
        None,
        "--section",
        "-s",
        help="Filter to a config section (e.g. memory, memory.embedding, scheduler)",
    ),
    output_format: str = typer.Option(
        "table", "--format", "-f", help="Output format: table, json"
    ),
    show_providers: bool = typer.Option(
        False, "--providers", help="Also show ProviderEntryConfig fields"
    ),
) -> None:
    """Print all configuration keys with types and defaults.

    Use --section to narrow down (e.g. -s memory.embedding).
    Use --providers to also expand the per-provider model schema.
    """
    entries = _build_schema(section, show_providers)

    if output_format == "json":
        console.out(
            json.dumps(
                [
                    {
                        "path": e.path,
                        "type": e.type_,
                        "default": e.default_,
                        "constraints": e.constraints,
                    }
                    for e in entries
                ],
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    table = Table(title="Configuration Schema", highlight=True)
    table.add_column("Path", style="cyan", no_wrap=True)
    table.add_column("Type", style="yellow")
    table.add_column("Default", style="green")
    for e in entries:
        constraints = f" [{e.constraints}]" if e.constraints != "-" else ""
        table.add_row(Text(e.path), Text(e.type_), Text(e.default_ + constraints))
    console.print(table)


def _build_schema(section: str | None, show_providers: bool) -> list[SchemaEntry]:
    """Build the schema entry list, optionally filtered."""
    entries: list[SchemaEntry] = []

    # Top-level scalar fields (skip the nested model containers)
    nested_keys = {*_NESTED_MODELS, "providers", "memory", "model_routing"}
    for fname, finfo in Settings.model_fields.items():
        if fname in nested_keys:
            continue
        entries.append(
            SchemaEntry(
                path=fname,
                type_=_human_type(finfo.annotation),
                default_=_field_default(finfo),
                constraints=_format_constraints(finfo),
            )
        )

    # Providers
    entries.append(
        SchemaEntry(
            path="providers", type_="dict[str, ProviderEntryConfig]", default_="{}"
        )
    )
    if show_providers:
        for e in walk_schema(ProviderEntryConfig, "providers.<id>"):
            entries.append(e)

    # Standard nested models
    for sub_path, model_cls in _NESTED_MODELS.items():
        if (
            section
            and not sub_path.startswith(section)
            and not (section.startswith(sub_path))
        ):
            continue
        for e in walk_schema(model_cls, sub_path):
            entries.append(e)

    # Memory has deeper nesting: MemoryConfig -> Retrieval / Embedding / Consolidation
    if not section or section.startswith("memory"):
        mem_entries = _build_memory_schema()
        entries.extend(mem_entries)

    if section:
        entries = [e for e in entries if e.path.startswith(section)]

    return entries


def _build_memory_schema() -> list[SchemaEntry]:
    """Walk the full MemoryConfig sub-tree."""
    from nahida_bot.core.config import (
        MemoryConfig,
    )

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


# ---------------------------------------------------------------------------
# config validate
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ValidationIssue:
    severity: str  # "error" | "warning"
    path: str
    message: str


@dataclass(slots=True)
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error")

    @property
    def warnings(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    @property
    def ok(self) -> bool:
        return self.errors == 0


def _legacy_model_spec(*, provider_id: str = "", model: str = "") -> str:
    """Build a model spec from legacy provider/model split fields."""
    provider_id = provider_id.strip()
    model = model.strip()
    if provider_id and model:
        if model.startswith(f"{provider_id}/"):
            return model
        return f"{provider_id}/{model}"
    return model


def _iter_config_models(settings: Settings) -> list[tuple[str, str, list[str]]]:
    """Return configured provider models as (provider_id, model_name, tags)."""
    models: list[tuple[str, str, list[str]]] = []
    for provider_id, entry in settings.providers.items():
        for model_entry in entry.models:
            if isinstance(model_entry, str):
                if model_entry:
                    models.append((provider_id, model_entry, []))
                continue
            name = getattr(model_entry, "name", "")
            if name:
                models.append((provider_id, name, list(model_entry.tags)))
    return models


def _provider_has_model(settings: Settings, provider_id: str, model: str) -> bool:
    return any(
        pid == provider_id and model_name == model
        for pid, model_name, _tags in _iter_config_models(settings)
    )


def _model_spec_resolves(settings: Settings, spec: str) -> bool:
    """Return whether a model spec can resolve by provider/model, bare name, or tag."""
    spec = spec.strip()
    if not spec:
        return False

    if "/" in spec:
        provider_id, _, bare_model = spec.partition("/")
        if provider_id in settings.providers:
            return _provider_has_model(settings, provider_id, bare_model)

    for _provider_id, model_name, tags in _iter_config_models(settings):
        if spec == model_name or spec in tags:
            return True

    # Match ModelRouter's implicit primary tag behavior.
    return spec == "primary" and any(
        entry.models for entry in settings.providers.values()
    )


def _add_unresolved_model_issue(
    report: ValidationReport,
    path: str,
    spec: str,
) -> None:
    report.issues.append(
        ValidationIssue(
            "error",
            path,
            f"Model spec '{spec}' does not match any provider/model, model name, or tag",
        )
    )


@config_app.command(name="validate")
def validate_cmd(
    config_yaml: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to YAML configuration file",
    ),
) -> None:
    """Validate configuration for common issues and inconsistencies.

    Checks include:
    - default_provider refers to a defined provider
    - internal model spec references can potentially resolve
    - provider entries have api_key set
    - sqlite-vec dependency and dimension setup
    - multimodal fallback model is set when fallback mode is enabled
    """
    try:
        settings = load_settings(config_yaml=config_yaml)
    except Exception as exc:
        from pydantic import ValidationError

        if isinstance(exc, ValidationError):
            console.print("[bold red]Configuration validation failed:[/bold red]\n")
            for error in exc.errors():
                loc = ".".join(str(p) for p in error["loc"])
                console.print(f"  [cyan]{loc}[/cyan]  {error['msg']}")
            console.print(
                "\n[yellow]Hint:[/yellow] Check that all "
                "${VAR} placeholders resolve to actual values."
            )
        else:
            console.print(f"[bold red]Failed to load config:[/bold red] {exc}")
        raise typer.Exit(1)

    report = _validate(settings)
    _print_report(report)

    if not report.ok:
        raise typer.Exit(1)


def _validate(settings: Settings) -> ValidationReport:
    report = ValidationReport()

    provider_ids = list(settings.providers.keys())

    if not provider_ids:
        report.issues.append(
            ValidationIssue("warning", "providers", "No LLM providers configured")
        )
    else:
        if settings.default_provider and settings.default_provider not in provider_ids:
            report.issues.append(
                ValidationIssue(
                    "error",
                    "default_provider",
                    f"'{settings.default_provider}' not found in providers: "
                    f"{', '.join(provider_ids)}",
                )
            )

        for pid, entry in settings.providers.items():
            if not entry.api_key:
                report.issues.append(
                    ValidationIssue(
                        "warning",
                        f"providers.{pid}.api_key",
                        f"Provider '{pid}' has no api_key set",
                    )
                )
            if not entry.models:
                report.issues.append(
                    ValidationIssue(
                        "warning",
                        f"providers.{pid}.models",
                        f"Provider '{pid}' has no models configured",
                    )
                )

    # --- multimodal ---
    mm = settings.multimodal
    if mm.image_fallback_mode != "off":
        fallback_spec = _legacy_model_spec(
            provider_id=mm.image_fallback_provider,
            model=mm.image_fallback_model,
        )
        if fallback_spec:
            if not _model_spec_resolves(settings, fallback_spec):
                _add_unresolved_model_issue(
                    report,
                    "multimodal.image_fallback_model",
                    fallback_spec,
                )
        elif mm.image_fallback_provider:
            if mm.image_fallback_provider not in settings.providers:
                report.issues.append(
                    ValidationIssue(
                        "error",
                        "multimodal.image_fallback_provider",
                        f"Provider '{mm.image_fallback_provider}' is not configured",
                    )
                )
        elif not _model_spec_resolves(settings, "vision"):
            report.issues.append(
                ValidationIssue(
                    "warning",
                    "multimodal.image_fallback_model",
                    "Image fallback is enabled but no fallback model/provider is "
                    "configured and no model has the 'vision' tag",
                )
            )

    # --- memory ---
    mem = settings.memory
    if mem.enabled:
        emb = mem.embedding
        if emb.enabled:
            embedding_spec = _legacy_model_spec(
                provider_id=emb.provider_id,
                model=emb.model,
            )
            if embedding_spec:
                if not _model_spec_resolves(settings, embedding_spec):
                    _add_unresolved_model_issue(
                        report,
                        "memory.embedding.model",
                        embedding_spec,
                    )
            elif emb.provider_id:
                report.issues.append(
                    ValidationIssue(
                        "warning",
                        "memory.embedding.provider_id",
                        "memory.embedding.provider_id without memory.embedding.model "
                        "is ignored; use memory.embedding.model: provider/model",
                    )
                )
                if not _model_spec_resolves(settings, "embedding"):
                    report.issues.append(
                        ValidationIssue(
                            "warning",
                            "memory.embedding",
                            "Embedding is enabled but no model has the 'embedding' tag",
                        )
                    )
            elif not _model_spec_resolves(settings, "embedding"):
                report.issues.append(
                    ValidationIssue(
                        "warning",
                        "memory.embedding",
                        "Embedding is enabled but no model has the 'embedding' tag, "
                        "and memory.embedding.model is not set",
                    )
                )

        ret = mem.retrieval
        if ret.vector_enabled and ret.vector_backend == "sqlite-vec":
            try:
                import sqlite_vec  # type: ignore[import-untyped]  # noqa: F401
            except ImportError:
                report.issues.append(
                    ValidationIssue(
                        "error",
                        "memory.retrieval.vector_backend",
                        "sqlite-vec backend requires 'pip install sqlite-vec'",
                    )
                )
            if not emb.enabled:
                report.issues.append(
                    ValidationIssue(
                        "warning",
                        "memory.embedding.enabled",
                        "sqlite-vec vector retrieval is enabled but memory embedding "
                        "is disabled",
                    )
                )
            elif emb.dimensions <= 0:
                report.issues.append(
                    ValidationIssue(
                        "warning",
                        "memory.embedding.dimensions",
                        "sqlite-vec vector retrieval will need to probe embedding "
                        "dimensions at startup; configure dimensions for deterministic setup",
                    )
                )

    # --- scheduler ---
    scheduler = settings.scheduler
    if scheduler.memory_dreaming_enabled:
        dreaming_spec = _legacy_model_spec(
            provider_id=scheduler.memory_dreaming_provider_id,
            model=scheduler.memory_dreaming_model,
        )
        if dreaming_spec and not _model_spec_resolves(settings, dreaming_spec):
            _add_unresolved_model_issue(
                report,
                "scheduler.memory_dreaming_model",
                dreaming_spec,
            )
        elif (
            scheduler.memory_dreaming_provider_id
            and not scheduler.memory_dreaming_model
        ):
            report.issues.append(
                ValidationIssue(
                    "warning",
                    "scheduler.memory_dreaming_provider_id",
                    "memory_dreaming_provider_id without memory_dreaming_model is "
                    "ignored; use memory_dreaming_model: provider/model",
                )
            )

    # --- channels ---
    extra = settings.model_extra or {}
    for channel_id in ("telegram", "milky"):
        if channel_id in extra:
            channel_cfg = extra[channel_id]
            if isinstance(channel_cfg, dict):
                if channel_id == "telegram" and not channel_cfg.get("bot_token"):
                    report.issues.append(
                        ValidationIssue(
                            "warning",
                            f"{channel_id}.bot_token",
                            "Telegram is configured but bot_token is not set",
                        )
                    )
                if channel_id == "milky" and not channel_cfg.get("access_token"):
                    report.issues.append(
                        ValidationIssue(
                            "warning",
                            f"{channel_id}.access_token",
                            "Milky is configured but access_token is not set",
                        )
                    )

    return report


def _print_report(report: ValidationReport) -> None:
    if not report.issues:
        console.print(
            "[bold green]OK - Configuration is valid, no issues found.[/bold green]"
        )
        return

    for issue in report.issues:
        if issue.severity == "error":
            prefix = "[bold red]ERROR[/bold red]"
        else:
            prefix = "[bold yellow]WARN [/bold yellow]"
        console.print(f"  {prefix}  [cyan]{issue.path}[/cyan]  {issue.message}")

    summary_color = "red" if report.errors else "yellow"
    console.print(
        f"\n[bold {summary_color}]{report.errors} error(s), {report.warnings} warning(s)[/bold {summary_color}]"
    )
