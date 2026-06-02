"""Plugin testing console — an interactive REPL for nahida-bot plugins.

Usage::

    python -m nahida_bot_sdk.testing.console ./plugins/my-plugin

Console built-in commands use the ``#`` prefix (distinct from ``/`` for
plugin commands):

    #exit / #quit        Exit the console
    #help                Show help
    #tools               List registered tools
    #commands            List registered commands
    #events              List subscribed event types
    #call <name> {json}  Invoke a registered tool with JSON arguments
    #fire <Type> {json}  Fire an event to registered handlers

Plugin interactions:

    /name args           Invoke a plugin-registered /command
    plain text ...        Simulate an inbound message (fires MessageReceived)

Future: ``#mode connect <bot-url>`` will connect the REPL to a running
nahida-bot instance, using its LLM config and live services instead of
MockBotAPI.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path

from nahida_bot_sdk.events import MessagePayload, MessageReceived
from nahida_bot_sdk.manifest import parse_manifest
from nahida_bot_sdk.testing import ConsoleMockBotAPI


def _header(text: str) -> None:
    print(f"\n  {text}")
    print("  " + "─" * 50)


def _help() -> None:
    print("""
  Plugin commands (forwarded to plugin):
    /name args          Invoke a registered /command

  Console commands (use # prefix):
    #help               Show this help
    #exit / #quit       Exit the console
    #tools              List registered tools
    #commands           List registered commands
    #events             List subscribed event types
    #call <name> {json} Invoke a registered tool with JSON arguments
    #fire <Type> {json} Fire an event to registered handlers

  Other:
    plain text ...       Simulate an inbound message (fires MessageReceived)
""")


async def run_console(plugin_dir: str) -> None:
    """Load a plugin from *plugin_dir* and start the interactive REPL."""
    root = Path(plugin_dir).resolve()
    if not (root / "plugin.yaml").is_file():
        print(f"Error: No plugin.yaml found in {root}")
        sys.exit(1)

    # Parse manifest
    try:
        manifest = parse_manifest(root / "plugin.yaml")
    except Exception as exc:
        print(f"Error: Failed to parse manifest: {exc}")
        sys.exit(1)

    # Ensure plugin dir is importable
    plugin_dir_str = str(root.resolve())
    if plugin_dir_str not in sys.path:
        sys.path.insert(0, plugin_dir_str)

    # Import the entry class
    module_path, class_name = manifest.entrypoint.rsplit(":", 1)
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        print(f"Error: Failed to import '{module_path}': {exc}")
        missing = _guess_missing_package(exc)
        if missing:
            print(f"  Hint: missing dependency? Try: uv pip install {missing}")
        sys.exit(1)

    entry_class = getattr(module, class_name, None)
    if entry_class is None:
        print(f"Error: Module '{module_path}' has no attribute '{class_name}'")
        sys.exit(1)

    # Instantiate plugin with console mock
    api = ConsoleMockBotAPI()
    plugin = entry_class(api=api, manifest=manifest)

    # Run on_load
    print("\n  nahida-bot-sdk Plugin Console")
    print(f"  Plugin: {manifest.name} v{manifest.version}")
    print(f"  ID: {manifest.id}")
    await plugin.on_load()

    # Show summary
    if api.tool_names:
        print(f"  Tools: {', '.join(api.tool_names)}")
    cmds = api.list_commands()
    if cmds:
        print(f"  Commands: {', '.join(c['name'] for c in cmds)}")
    if api.event_handler_types:
        print(f"  Events:  {', '.join(t.__name__ for t in api.event_handler_types)}")

    print("  Type #help for commands")
    print()

    # REPL loop
    while True:
        try:
            raw = input("  \033[1mYou >\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Goodbye!")
            break

        if not raw:
            continue

        # ── Console built-in commands (# prefix) ──────

        if raw.startswith("#"):
            parts = raw[1:].split(maxsplit=1)
            cmd = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""

            if cmd in ("exit", "quit", "q"):
                print("  Goodbye!")
                break

            if cmd == "help":
                _help()
                continue

            if cmd == "tools":
                _show_tools(api)
                continue

            if cmd == "commands":
                _show_commands(api)
                continue

            if cmd == "events":
                _show_events(api)
                continue

            if cmd == "call":
                await _handle_call(api, args)
                continue

            if cmd == "fire":
                await _handle_fire(api, args)
                continue

            if cmd == "mode":
                print(
                    f"  Mode '{args}' is not yet implemented (future: connect to live bot)"
                )
                continue

            print(
                f"  Unknown console command: #{cmd}. Type #help for available commands."
            )
            continue

        # ── Plugin command (/ prefix) ─────────────────

        if raw.startswith("/"):
            cmd_parts = raw[1:].split(maxsplit=1)
            cmd_name = cmd_parts[0]
            cmd_args = cmd_parts[1] if len(cmd_parts) > 1 else ""
            result = await api.invoke_command(cmd_name, cmd_args)
            print(f"  \033[1mBot\033[0m > {_safe_encode(str(result))}")
            continue

        # ── Plain text — simulate MessageReceived ─────

        msg = MessagePayload(
            message=raw,
            session_id="console:private:test",
        )
        event = MessageReceived(payload=msg)
        await api._trigger_event(event)

        while api.sent_messages:
            target, outbound = api.sent_messages.pop(0)
            print(f"  \033[1mBot\033[0m > {_safe_encode(outbound.text)}")


# ── Console command handlers ──────────────────────────


def _show_tools(api: ConsoleMockBotAPI) -> None:
    if not api.tool_names:
        print("  No tools registered.")
        return
    print(f"\n  Registered tools ({len(api.tool_names)}):")
    for name in api.tool_names:
        info = api._tools[name]
        params = info.get("parameters", {})
        param_str = ", ".join(params.get("properties", {}).keys())
        print(f"    {name}({param_str}) — {info['description']}")
    print()


def _show_commands(api: ConsoleMockBotAPI) -> None:
    cmds = api.list_commands()
    if not cmds:
        print("  No commands registered.")
        return
    print(f"\n  Registered commands ({len(cmds)}):")
    for c in cmds:
        aliases = f" (aliases: {', '.join(c['aliases'])})" if c["aliases"] else ""
        print(f"    /{c['name']} — {c['description']}{aliases}")
    print()


def _show_events(api: ConsoleMockBotAPI) -> None:
    types = api.event_handler_types
    if not types:
        print("  No event handlers registered.")
        return
    print(f"\n  Subscribed event types ({len(types)}):")
    for t in types:
        print(f"    {t.__name__}")
    print()


async def _handle_call(api: ConsoleMockBotAPI, args: str) -> None:
    """Handle #call <tool_name> [json_args]"""
    parts = args.strip().split(maxsplit=1)
    if not parts or not parts[0]:
        print('  Usage: #call <tool_name> {"arg": "value"}')
        return
    tool_name = parts[0]
    try:
        arguments = json.loads(parts[1]) if len(parts) > 1 else {}
    except json.JSONDecodeError as exc:
        print(f"  \033[31m[Error]\033[0m Invalid JSON: {exc}")
        return
    result = await api.invoke_tool(tool_name, arguments)
    print(f"  \033[33m[{tool_name}]\033[0m {_safe_encode(result)}")


async def _handle_fire(api: ConsoleMockBotAPI, args: str) -> None:
    """Handle #fire <EventType> [json_payload]"""
    parts = args.strip().split(maxsplit=1)
    if not parts or not parts[0]:
        print('  Usage: #fire <EventType> {"payload": "json"}')
        return
    event_type_name = parts[0]

    # Look up by subscribed handler types
    matching = [t for t in api.event_handler_types if t.__name__ == event_type_name]
    if not matching:
        try:
            from nahida_bot_sdk import events as ev

            event_cls = getattr(ev, event_type_name, None)
        except AttributeError:
            event_cls = None
        if event_cls is None:
            print(
                f"  \033[31m[Error]\033[0m Event type '{event_type_name}' "
                f"not found and no handlers subscribed to it"
            )
            return
        matching = [event_cls]

    event_cls = matching[0]
    try:
        if len(parts) > 1:
            payload_data = json.loads(parts[1])
        else:
            payload_data = {}

        # Resolve the proper payload class for known events
        payload = _build_payload(event_type_name, payload_data)
        event = event_cls(payload=payload)

    except Exception as exc:
        print(f"  \033[31m[Error]\033[0m Failed to construct event: {exc}")
        return
    await api._trigger_event(event)
    print(f"  \033[36m[Event]\033[0m Fired {event_type_name}")


def _build_payload(event_type_name: str, data: dict[str, object]) -> object:  # noqa: S106
    """Construct the correct payload instance for an event type.

    Falls back to the raw dict if the payload type is unknown.
    """
    from nahida_bot_sdk import events as ev

    # Map of event type name → payload class
    payload_cls = getattr(ev, f"{event_type_name}Payload", None)
    if payload_cls is not None:
        return payload_cls(**data)

    # Special case: message events all use MessagePayload
    if event_type_name.startswith("Message"):
        return MessagePayload(**data)

    # Fallback: raw dict
    return data


# ── Helpers ───────────────────────────────────────────


def _guess_missing_package(exc: ImportError | ModuleNotFoundError) -> str | None:
    """Try to extract the missing package name from an import error."""
    msg = str(exc)
    if "No module named" in msg:
        name = msg.split("No module named ")[-1].strip("'\"")
        return name.split(".")[0]
    return None


def _safe_encode(s: str) -> str:
    """Replace characters that can't be printed in the current terminal encoding."""
    encoding = sys.stdout.encoding or "utf-8"
    try:
        s.encode(encoding)
        return s
    except UnicodeEncodeError:
        return s.encode(encoding, errors="replace").decode(encoding)


def main() -> None:
    """Entry point: python -m nahida_bot_sdk.testing.console <plugin-dir>"""
    if len(sys.argv) < 2:
        print("Usage: python -m nahida_bot_sdk.testing.console <plugin-dir>")
        sys.exit(1)
    asyncio.run(run_console(sys.argv[1]))


if __name__ == "__main__":
    main()
