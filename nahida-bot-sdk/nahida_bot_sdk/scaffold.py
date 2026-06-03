"""Interactive scaffold for creating nahida-bot plugins.

Usage::

    # Interactive mode (prompts for every choice)
    python -m nahida_bot_sdk.scaffold my-plugin

    # Non-interactive — skip prompts, use defaults
    python -m nahida_bot_sdk.scaffold my-plugin --yes

    # Specify output directory
    python -m nahida_bot_sdk.scaffold my-plugin --target ./plugins

Generated structure::

    my-plugin/
    ├── pyproject.toml
    ├── plugin.yaml
    ├── README.md
    └── nahida_plugin_my_plugin/
        ├── __init__.py
        └── plugin.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

TEMPLATE_PYPROJECT = """\
[project]
name = {pkg_name}
version = {version}
description = {description}
requires-python = {python_requirement}
dependencies = [
    {sdk_dependency},{extra_deps}
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = [{module_name}]
"""

TEMPLATE_PLUGIN_YAML = """\
id: {plugin_id}
name: {display_name}
version: {version}
description: {description}
entrypoint: {entrypoint}
load_phase: "post-agent"
permissions:{permissions_block}
capabilities:{capabilities_block}
"""

# ── helpers ────────────────────────────────────────────


def _module_name(name: str) -> str:
    """Convert 'my-plugin' to 'nahida_plugin_my_plugin'."""
    snake = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    return f"nahida_plugin_{snake}"


def _class_name(name: str) -> str:
    """Convert 'my-plugin' to 'MyPlugin'."""
    parts = re.split(r"[^a-zA-Z0-9]", name)
    return "".join(p.capitalize() for p in parts if p)


def _pkg_name(name: str) -> str:
    """Convert 'my-plugin' to 'nahida-plugin-my-plugin' — PyPI package name."""
    return f"nahida-plugin-{name}"


def _plugin_id(name: str) -> str:
    """Convert 'my-plugin' to 'my-plugin' — plugin id."""
    return name


def _quoted_string(value: str) -> str:
    """Return a safely escaped double-quoted YAML/TOML string."""
    return json.dumps(str(value), ensure_ascii=False)


def _docstring_content(value: str) -> str:
    """Escape only what can break a generated triple-double-quoted docstring."""
    return str(value).replace("\\", "\\\\").replace('"""', '\\"\\"\\"')


# ── interactive prompts ────────────────────────────────


def _ask(prompt: str, default: str = "") -> str:
    """Prompt with an optional default value."""
    if default:
        result = input(f"  {prompt} [{default}]: ").strip()
        return result or default
    result = input(f"  {prompt}: ").strip()
    return result


def _confirm(prompt: str, default: bool = False) -> bool:
    """Y/n or y/N prompt."""
    hint = "Y/n" if default else "y/N"
    result = input(f"  {prompt} [{hint}]: ").strip().lower()
    if not result:
        return default
    return result in ("y", "yes")


# ── generators ─────────────────────────────────────────


def _generate_permissions_block(perms: dict[str, bool | list[str]]) -> str:
    """Build the YAML permissions block with only non-default values."""
    lines: list[str] = []

    # network
    outbound_raw = perms.get("network_outbound", [])
    outbound: list[str] = outbound_raw if isinstance(outbound_raw, list) else []
    inbound_raw = perms.get("network_inbound", False)
    inbound: bool = inbound_raw if isinstance(inbound_raw, bool) else False
    if outbound or inbound:
        lines.append("  network:")
        if outbound:
            quoted = [_quoted_string(p) for p in outbound]
            lines.append(f"    outbound: [{', '.join(quoted)}]")
        if inbound:
            lines.append("    inbound: true")

    # filesystem
    fs_read_raw = perms.get("filesystem_read", [])
    fs_read: list[str] = fs_read_raw if isinstance(fs_read_raw, list) else []
    fs_write_raw = perms.get("filesystem_write", [])
    fs_write: list[str] = fs_write_raw if isinstance(fs_write_raw, list) else []
    if fs_read or fs_write:
        lines.append("  filesystem:")
        if fs_read:
            quoted = [_quoted_string(z) for z in fs_read]
            lines.append(f"    read: [{', '.join(quoted)}]")
        if fs_write:
            quoted = [_quoted_string(z) for z in fs_write]
            lines.append(f"    write: [{', '.join(quoted)}]")

    # memory
    mem_read = perms.get("memory_read", False)
    mem_write = perms.get("memory_write", False)
    if mem_read or mem_write:
        lines.append("  memory:")
        if mem_read:
            lines.append("    read: true")
        if mem_write:
            lines.append("    write: true")

    # system
    subprocess = perms.get("subprocess", False)
    if subprocess:
        lines.append("  system:")
        lines.append("    subprocess: true")

    # llm
    llm = perms.get("llm_access", False)
    if llm:
        lines.append("  llm_access: true")

    if not lines:
        return " {}"

    return "\n" + "\n".join(lines)


def _generate_capabilities_block(
    has_commands: bool,
    has_tools: list[str] | None,
    subscribes_to: list[str] | None,
) -> str:
    """Build the YAML capabilities block."""
    lines: list[str] = []
    if has_tools:
        lines.append("  tools:")
        for t in has_tools:
            lines.append(f"    - name: {_quoted_string(t)}")
            lines.append('      description: ""')
    if subscribes_to:
        lines.append("  subscribes_to:")
        for s in subscribes_to:
            lines.append(f"    - {_quoted_string(s)}")

    if not lines:
        return " {}"
    return "\n" + "\n".join(lines)


TEMPLATE_README = """\
# {display_name}

{description}

## Install

```bash
uv pip install {install_path}
```

## Commands

<!-- Document your slash commands here -->

## Tools

<!-- Document the tools your plugin exposes to LLMs -->

## Events

<!-- List any events your plugin subscribes to or publishes -->
"""

# ── Python code templates — use ''' outer, """ inner for docstrings ──

# fmt: off
TEMPLATE_PLUGIN_PY = '''\
"""{description}"""

from __future__ import annotations

{imports}

class {class_name}(Plugin):
    """{description}"""{class_body}{handlers}'''

TEMPLATE_LLM_COMMENT = "\n    # Use self.api.llm_chat() or self.api.run_subagent(...)"
TEMPLATE_SCHEDULER_COMMENT = "\n    # Use self.api.scheduler_service.add_job(...)"

_TEMPLATE_CLASS_BODY_PREFIX = "\n"  # blank line after docstring before comment block

TEMPLATE_CMD_HANDLER = '''\
    @register_command("{cmd_name}", description="{description}")
    async def _cmd_{cmd_name}(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> CommandResult:
        """Handle /{cmd_name}."""
        return CommandResult(OutboundMessage(text=f"Got: {{args}}"))'''

TEMPLATE_TOOL_HANDLER = '''\
    @register_tool("{tool_name}", description="TODO: describe what this tool does")
    async def _handle_{tool_name}(self, **kwargs: object) -> str:
        """Handle {tool_name} tool calls."""
        return "TODO: implement {tool_name}" '''

TEMPLATE_EVENT_HANDLER = '''\
    @subscribe({event_type})
    async def _on_{event_lower}(self, event: {event_type}) -> None:
        """Handle {event_type} events."""
        pass'''
# fmt: on


def _generate_plugin_py(
    class_name: str,
    description: str,
    has_commands: bool,
    has_tools: bool,
    has_events: bool,
    has_llm: bool,
    has_scheduler: bool,
) -> str:
    """Generate the plugin.py skeleton based on capabilities."""
    imports: set[str] = {
        "CommandResult",
        "InboundMessage",
        "OutboundMessage",
    }
    handler_stubs: list[str] = []

    if has_commands:
        imports.add("register_command")
        handler_stubs.append(
            TEMPLATE_CMD_HANDLER.format(
                cmd_name="example", description="Example command"
            )
        )

    if has_tools:
        imports.add("register_tool")
        handler_stubs.append(TEMPLATE_TOOL_HANDLER.format(tool_name="my_tool"))

    if has_events:
        imports.add("subscribe")
        imports.add("MessageReceived")
        handler_stubs.append(
            TEMPLATE_EVENT_HANDLER.format(
                event_type="MessageReceived", event_lower="message"
            )
        )

    # Class body hints
    class_body_parts: list[str] = []
    if has_llm:
        class_body_parts.append(TEMPLATE_LLM_COMMENT)
    if has_scheduler:
        class_body_parts.append(TEMPLATE_SCHEDULER_COMMENT)
    class_body = "".join(class_body_parts)
    if class_body:
        class_body = _TEMPLATE_CLASS_BODY_PREFIX + class_body

    # Build import block — sorted for determinism
    if "Plugin" not in imports:
        imports.add("Plugin")
    sorted_imports = sorted(imports)
    imports_block = "\n".join(f"    {imp}," for imp in sorted_imports)
    imports_block = f"from nahida_bot_sdk import (\n{imports_block}\n)"

    handlers_block = ""
    if handler_stubs:
        handlers_block = "\n\n" + "\n\n".join(handler_stubs)

    return TEMPLATE_PLUGIN_PY.format(
        description=description,
        imports=imports_block,
        class_name=class_name,
        class_body=class_body,
        handlers=handlers_block,
    )


# ── version detection ──────────────────────────────────


def _detect_python_version() -> str:
    """Return the current Python version as a PEP 440 constraint."""
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def _detect_sdk_version() -> str:
    """Return the installed nahida-bot-sdk version, or a safe default."""
    try:
        from importlib.metadata import version

        return version("nahida-bot-sdk")
    except Exception:
        return "0.1.0"


# ── main entry point ───────────────────────────────────


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Scaffold a new nahida-bot plugin",
    )
    parser.add_argument(
        "name",
        nargs="?",
        help="Plugin name (kebab-case, e.g. 'my-plugin')",
    )
    parser.add_argument(
        "--target",
        default=".",
        help="Output directory (default: current directory)",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip prompts, generate with defaults",
    )
    parser.add_argument(
        "--python-version",
        default=_detect_python_version(),
        help="Minimum Python version (default: auto-detect current)",
    )
    parser.add_argument(
        "--sdk-version",
        default=_detect_sdk_version(),
        help="Minimum nahida-bot-sdk version (default: auto-detect installed)",
    )
    parser.add_argument(
        "--install",
        "-i",
        action="store_true",
        help="Run 'uv pip install' after scaffolding",
    )
    args = parser.parse_args(argv)

    name = args.name
    if not name:
        name = input("Plugin name (kebab-case, e.g. 'my-plugin'): ").strip()
    if not name:
        print("Error: plugin name is required.")
        sys.exit(1)

    if not re.match(r"^[a-z][a-z0-9-]*$", name):
        print(
            "Error: plugin name must start with a letter and contain only "
            "lowercase letters, digits, and hyphens."
        )
        sys.exit(1)

    module_name = _module_name(name)
    class_name = _class_name(name)
    pkg_name = _pkg_name(name)
    plugin_id = _plugin_id(name)

    print()
    print("  nahida-bot Plugin Scaffold")
    print(f"  {'-' * 30}")
    print()

    # ── Interactive prompts ──────────────────────────

    if args.yes:
        description = f"A nahida-bot plugin: {name}"
        version = "0.1.0"
        has_commands = True
        has_tools = False
        tool_names: list[str] = []
        has_events = False
        event_names: list[str] = []
        perms = {
            "filesystem_read": ["workspace"],
        }
    else:
        description = _ask("Description", f"A nahida-bot plugin: {name}")
        version = _ask("Version", "0.1.0")

        print()
        print("  -- Permissions --")
        print()

        net_outbound: list[str] = []
        if _confirm("Network — outbound access?", default=False):
            outbound_input = _ask(
                "  Allowed URL patterns (space-separated, * = all)", "*"
            )
            net_outbound = outbound_input.split()

        net_inbound = _confirm("Network — inbound (HTTP server)?", default=False)

        fs_read: list[str] = []
        if _confirm("Filesystem — read workspace?", default=True):
            fs_read = ["workspace"]
        fs_write: list[str] = []
        if _confirm("Filesystem — write workspace?", default=False):
            fs_write = ["workspace"]

        mem_read = _confirm("Memory — read?", default=False)
        mem_write = _confirm("Memory — write?", default=False)
        llm = _confirm("LLM access (api.llm_chat / run_subagent)?", default=False)
        subprocess = _confirm("Subprocess (exec)?", default=False)

        perms: dict[str, bool | list[str]] = {}
        if net_outbound:
            perms["network_outbound"] = net_outbound
        if net_inbound:
            perms["network_inbound"] = net_inbound
        if fs_read:
            perms["filesystem_read"] = fs_read
        if fs_write:
            perms["filesystem_write"] = fs_write
        if mem_read:
            perms["memory_read"] = mem_read
        if mem_write:
            perms["memory_write"] = mem_write
        if llm:
            perms["llm_access"] = llm
        if subprocess:
            perms["subprocess"] = subprocess

        print()
        print("  -- Capabilities --")
        print()

        has_commands = _confirm("Register commands (/xxx)?", default=True)
        has_tools = _confirm("Register tools (for LLM agent use)?", default=False)
        tool_names = []
        if has_tools:
            tool_input = _ask("  Tool names (space-separated)", "")
            tool_names = tool_input.split() if tool_input else ["my_tool"]

        has_events = _confirm("Subscribe to events?", default=False)
        event_names = []
        if has_events:
            event_input = _ask(
                "  Event types (space-separated, e.g. MessageReceived)",
                "MessageReceived",
            )
            event_names = event_input.split() if event_input else []

        print()

    # ── Generate ─────────────────────────────────────

    target = Path(args.target).resolve()
    plugin_dir = target / name
    src_dir = plugin_dir / module_name

    if plugin_dir.exists():
        print(f"Error: directory '{plugin_dir}' already exists.")
        sys.exit(1)

    # extra deps
    extra_deps = ""
    if "aiohttp" in str(perms):
        extra_deps += f"\n    {_quoted_string('aiohttp>=3.0')},"

    # pyproject.toml
    pyproject = TEMPLATE_PYPROJECT.format(
        pkg_name=_quoted_string(pkg_name),
        version=_quoted_string(version),
        description=_quoted_string(description),
        extra_deps=extra_deps,
        module_name=_quoted_string(module_name),
        python_requirement=_quoted_string(f">={args.python_version}"),
        sdk_dependency=_quoted_string(f"nahida-bot-sdk>={args.sdk_version}"),
    )

    # plugin.yaml
    permissions_block = _generate_permissions_block(perms)
    capabilities_block = _generate_capabilities_block(
        has_commands=has_commands,
        has_tools=tool_names if has_tools else None,
        subscribes_to=event_names if has_events else None,
    )

    plugin_yaml = TEMPLATE_PLUGIN_YAML.format(
        plugin_id=_quoted_string(plugin_id),
        display_name=_quoted_string(name.replace("-", " ").title()),
        version=_quoted_string(version),
        description=_quoted_string(description),
        entrypoint=_quoted_string(f"{module_name}.plugin:{class_name}"),
        permissions_block=permissions_block,
        capabilities_block=capabilities_block,
    )

    # plugin.py
    has_scheduler = False  # future
    plugin_py = _generate_plugin_py(
        class_name=class_name,
        description=_docstring_content(description),
        has_commands=has_commands,
        has_tools=has_tools,
        has_events=has_events,
        has_llm=bool(perms.get("llm_access", False)),
        has_scheduler=has_scheduler,
    )

    # Write files
    src_dir.mkdir(parents=True)

    (plugin_dir / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    print("  [+] pyproject.toml")
    (plugin_dir / "plugin.yaml").write_text(plugin_yaml, encoding="utf-8")
    print("  [+] plugin.yaml")
    (src_dir / "__init__.py").write_text(
        '"""' + _docstring_content(description) + '"""\n', encoding="utf-8"
    )
    print(f"  [+] {module_name}/__init__.py")
    (src_dir / "plugin.py").write_text(plugin_py, encoding="utf-8")
    print(f"  [+] {module_name}/plugin.py")

    # README.md
    readme = TEMPLATE_README.format(
        display_name=name.replace("-", " ").title(),
        description=description,
        install_path=plugin_dir.as_posix(),
    )
    (plugin_dir / "README.md").write_text(readme, encoding="utf-8")
    print("  [+] README.md")

    print()
    print(f"  Done! Plugin scaffolded at {plugin_dir}")

    if args.install:
        print()
        print("  Installing with uv pip install ...")
        import subprocess

        result = subprocess.run(
            ["uv", "pip", "install", str(plugin_dir)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print("  Installed successfully.")
        else:
            print(f"  Install failed (code {result.returncode}):")
            print(result.stderr.strip())
    else:
        print()
        print("  Install with:")
        print(f"    uv pip install {plugin_dir}")

    if args.target != ".":
        print()
        print("  Add to root pyproject.toml:")
        print("    [project.optional-dependencies]")
        print(f'    builtin-plugins = ["{pkg_name}"]')
        print("    [tool.uv.sources]")
        print(f'    {pkg_name} = {{ path = "./plugins/{name}" }}')


if __name__ == "__main__":
    main()
