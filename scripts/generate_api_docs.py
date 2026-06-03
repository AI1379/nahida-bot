"""Generate Markdown API docs from nahida_bot_sdk source code using griffe.

Usage:
    uv run python scripts/generate_api_docs.py
    # or
    python scripts/generate_api_docs.py

Output: docs/plugin-api/auto/*.md
"""

from __future__ import annotations

import sys
from pathlib import Path

import griffe
from jinja2 import Environment

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
SDK_SRC = ROOT / "nahida-bot-sdk"
DOCS_OUT = ROOT / "docs" / "plugin-api" / "auto"

# Order matters: modules are listed in the order they should appear in the sidebar.
MODULES: list[tuple[str, str, str]] = [
    ("nahida_bot_sdk.api", "api", "BotAPI 协议"),
    ("nahida_bot_sdk.messaging", "messaging", "消息类型"),
    ("nahida_bot_sdk.events", "events", "事件系统"),
    ("nahida_bot_sdk.commands", "commands", "命令相关"),
    ("nahida_bot_sdk.plugin", "plugin", "Plugin 基类"),
    ("nahida_bot_sdk.manifest", "manifest", "Manifest 清单"),
    ("nahida_bot_sdk.chat_address", "chat-address", "聊天地址与会话"),
    ("nahida_bot_sdk.testing._mocks", "testing", "测试工具"),
]

# ---------------------------------------------------------------------------
# Jinja2 templates (embedded — no external template files needed)
# ---------------------------------------------------------------------------

INDEX_TEMPLATE = """---
title: "API 参考（自动生成）"
description: 从 nahida_bot_sdk 源码自动生成的 API 参考文档。
---

# API 参考（自动生成）

> 本目录下的文档由 `scripts/generate_api_docs.py` 从 `nahida_bot_sdk` 源码自动生成。
> 不要手动编辑；修改源码后运行脚本重新生成。

## 模块

{% for mod in modules %}
- **[{{ mod.title }}]({{ mod.slug }})** — `{{ mod.module_path }}`
{%- endfor %}
"""

MODULE_TEMPLATE = """---
title: "{{ title }}"
description: 从 {{ module_path }} 自动生成的 API 参考。
---

# {{ title }}

> **源码路径:** `{{ module_path }}`
{%- if module_docstring %}

{{ module_docstring }}
{%- endif %}

---

{% if classes %}
## 类
{% for cls in classes %}

### {{ cls.name }}
{%- if cls.docstring %}

{{ cls.docstring }}
{%- endif %}
{%- if cls.bases %}

- **基类:** {{ cls.bases }}
{%- endif %}
{%- if cls.fields %}

| 字段 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
{%- for f in cls.fields %}
| `{{ f.name }}` | `{{ f.type }}` | `{{ f.default }}` | {{ f.docstring }} |
{%- endfor %}

{%- endif %}
{%- if cls.properties %}

**属性 (Properties):**
{%- for p in cls.properties %}

#### {{ p.name }}

- **返回类型:** `{{ p.type }}`
{%- if p.docstring %}

{{ p.docstring }}
{%- endif %}
{%- endfor %}
{%- endif %}
{%- if cls.methods %}

**方法:**
{%- for m in cls.methods %}

#### `{{ m.name }}({{ m.signature }})`
{%- if m.docstring %}

{{ m.docstring }}
{%- endif %}
{%- endfor %}
{%- endif %}
{% endfor %}
{%- endif %}
{%- if functions %}

## 函数
{% for f in functions %}

### `{{ f.name }}({{ f.signature }})`
{%- if f.docstring %}

{{ f.docstring }}
{%- endif %}
{%- endfor %}
{%- endif %}
{%- if type_aliases %}

## 类型别名
{% for ta in type_aliases %}

- `{{ ta.name }}`
{%- endfor %}
{%- endif %}
"""

# ---------------------------------------------------------------------------
# Data extraction helpers
# ---------------------------------------------------------------------------


def _annotation_str(ann) -> str | None:
    """Convert a griffe annotation (ExprName, ExprSubscript, str, or None) to a clean string."""
    if ann is None:
        return None
    if isinstance(ann, str):
        return ann
    try:
        return str(ann)
    except Exception:
        return None


def _clean_type(annotation: str | None) -> str:
    """Clean up a type annotation string for display."""
    if annotation is None:
        return "—"
    # Strip the module prefix for types from nahida_bot_sdk
    return annotation.replace("nahida_bot_sdk.", "")


def _format_default(value: str | None) -> str:
    """Format a default value for display in a table."""
    if value is None:
        return "—"
    s = str(value)
    # Clean up common factory patterns
    if "field(default_factory=" in s:
        return "`[]`" if "list" in s else "`{}`"
    if s == "()":
        return "`()`"
    if len(s) > 40:
        return f"`{s[:37]}...`"
    return f"`{s}`"


def _extract_docstring(obj) -> str:
    """Extract and clean a docstring from a griffe object."""
    if obj.docstring and obj.docstring.value:
        text = obj.docstring.value.strip()
        # RST literal markers `` -> `
        text = text.replace("``", "`")
        return text
    return ""


def _extract_module_info(mod) -> dict:
    """Extract classes, functions, and type aliases from a griffe module."""
    classes = []
    functions = []
    type_aliases: list[dict] = []

    for name, member in mod.members.items():
        if name.startswith("_"):
            continue  # skip private members

        if isinstance(member, griffe.Alias):
            continue  # skip re-exports from other modules
        if isinstance(member, griffe.Class):
            classes.append(_extract_class(member))
        elif isinstance(member, griffe.Function):
            functions.append(_extract_function(member))
        elif isinstance(member, griffe.Attribute):
            # Type aliases and constants (e.g. CommandHandlerResult)
            ann_str = _annotation_str(member.annotation)
            if ann_str:
                type_aliases.append(
                    {
                        "name": name,
                        "type": _clean_type(ann_str),
                    }
                )

    return {
        "module_docstring": _extract_docstring(mod),
        "classes": classes,
        "functions": functions,
        "type_aliases": type_aliases,
    }


def _is_dataclass(cls) -> bool:
    """Check if a griffe Class is a @dataclass based on its decorators."""
    for d in cls.decorators:
        if d.value and "dataclass" in str(d.value):
            return True
    return False


def _extract_class(cls) -> dict:
    """Extract class details from a griffe Class object."""
    is_dc = _is_dataclass(cls)

    # Determine bases
    bases = []
    if cls.bases:
        for b in cls.bases:
            # griffe v2 ExprName has .name for simple class references
            base_name = getattr(b, "name", None) or str(b)
            base_name = _clean_type(base_name)
            if base_name not in ("object",):
                bases.append(f"`{base_name}`")

    # Extract fields and properties from Attribute members.
    # For @dataclass classes, Attributes are fields. For others, they're properties.
    fields: list[dict] = []
    properties: list[dict] = []
    for name, attr in cls.members.items():
        if name.startswith("_"):
            continue
        if isinstance(attr, griffe.Attribute):
            entry = {
                "name": name,
                "type": _clean_type(_annotation_str(attr.annotation)),
                "default": _format_default(attr.value),
                "docstring": _extract_docstring(attr),
            }
            if is_dc:
                fields.append(entry)
            else:
                properties.append(entry)

    # Extract methods
    methods: list[dict] = []
    for name, member in cls.members.items():
        if name.startswith("_") or name in {"__init__", "__post_init__"}:
            continue
        if isinstance(member, griffe.Function):
            methods.append(_extract_function(member))

    return {
        "name": cls.name,
        "docstring": _extract_docstring(cls),
        "bases": ", ".join(bases) if bases else "",
        "fields": fields,
        "methods": methods,
        "properties": properties,
    }


def _extract_function(func) -> dict:
    """Extract function/method signature and docs."""
    params = []
    for p in func.parameters:
        param_str = _parameter_str(p)
        if param_str:
            params.append(param_str)
    signature = ", ".join(params)

    returns = _clean_type(_annotation_str(func.returns))

    return {
        "name": func.name,
        "signature": signature,
        "returns": returns,
        "docstring": _extract_docstring(func),
    }


def _parameter_str(param) -> str:
    """Format a single parameter as a signature string segment."""
    name = param.name
    if name in ("self", "cls"):
        return ""  # skip self/cls in display

    type_str = _clean_type(_annotation_str(param.annotation))

    result = name
    if type_str:
        result += f": {type_str}"

    if param.default and param.default != "...":
        default_val = str(param.default)
        result += f" = {default_val}"

    return result


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _simple_render(template_str: str, **kwargs) -> str:
    """Render a Jinja2 template string with given context."""
    env = Environment()
    env.filters.clear()  # keep it minimal
    template = env.from_string(template_str)
    return template.render(**kwargs)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print("Generating API documentation...")
    DOCS_OUT.mkdir(parents=True, exist_ok=True)

    # Collect module info for index
    module_entries: list[dict] = []

    for module_path, slug, title in MODULES:
        print(f"  Processing {module_path} ...")

        try:
            mod = griffe.load(module_path, search_paths=[str(SDK_SRC)])
        except Exception as exc:
            print(f"    ERROR loading {module_path}: {exc}")
            continue

        info = _extract_module_info(mod)

        # Render module page
        rendered = _simple_render(
            MODULE_TEMPLATE,
            title=title,
            module_path=module_path,
            **info,
        )

        out_path = DOCS_OUT / f"{slug}.md"
        out_path.write_text(rendered, encoding="utf-8")
        print(
            f"    -> {out_path.relative_to(ROOT)} ({len(info['classes'])} classes, {len(info['functions'])} functions)"
        )

        module_entries.append(
            {
                "slug": slug,
                "title": title,
                "module_path": module_path,
            }
        )

    # Render index
    index_rendered = _simple_render(INDEX_TEMPLATE, modules=module_entries)
    index_path = DOCS_OUT / "index.md"
    index_path.write_text(index_rendered, encoding="utf-8")
    print(f"  Index -> {index_path.relative_to(ROOT)}")

    print("Done!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
