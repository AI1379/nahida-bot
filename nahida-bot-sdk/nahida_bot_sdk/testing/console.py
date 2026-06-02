"""Plugin testing console — an interactive REPL for nahida-bot plugins.

Usage::

    python -m nahida_bot_sdk.testing.console ./plugins/my-plugin

Provides a simple chat-like interface where you can:
- Type text to simulate inbound messages (fires MessageReceived handlers)
- Type ``/name args`` to invoke registered commands
- Type ``tool:name {"arg":"val"}`` to invoke registered tools
- Type ``event:Type {"payload": ...}`` to fire arbitrary events
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path

from nahida_bot_sdk.events import MessageReceived, MessagePayload
from nahida_bot_sdk.manifest import parse_manifest
from nahida_bot_sdk.testing import ConsoleMockBotAPI


def _header(text: str) -> None:
    print(f"\n  {text}")
    print("  " + "─" * 50)


def _help() -> None:
    print("""
  Commands:
    /help              Show this help
    /quit              Exit the console
    /tools             List registered tools
    /commands          List registered commands
    /events            List subscribed event types

  Interactions:
    text ...           Simulate an inbound message (fires MessageReceived)
    /name args         Invoke a registered /command
    tool:name {...}    Invoke a registered tool with JSON arguments
    event:Type {...}   Fire an event to registered handlers
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
        print(f"  Tools registered: {', '.join(api.tool_names)}")
    cmds = api.list_commands()
    if cmds:
        print(f"  Commands registered: {', '.join(c['name'] for c in cmds)}")
    if api.event_handler_types:
        print(
            f"  Events subscribed: {', '.join(t.__name__ for t in api.event_handler_types)}"
        )

    print("  Type /help for commands")
    print()

    # REPL loop
    while True:
        try:
            raw = input("  \033[1mYou\033[0m: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Goodbye!")
            break

        if not raw:
            continue

        # Built-in console commands (no leading / or with leading /)
        lowered = raw.lower()

        if lowered in ("/quit", "/exit", ":q"):
            print("  Goodbye!")
            break
        if lowered in ("/help", "help"):
            _help()
            continue
        if lowered in ("/tools", "tools"):
            if api.tool_names:
                print(f"\n  Registered tools ({len(api.tool_names)}):")
                for name in api.tool_names:
                    info = api._tools[name]
                    params = info.get("parameters", {})
                    param_str = ", ".join(params.get("properties", {}).keys())
                    print(f"    {name}({param_str}) — {info['description']}")
            else:
                print("  No tools registered.")
            print()
            continue
        if lowered in ("/commands", "commands"):
            cmds = api.list_commands()
            if cmds:
                print(f"\n  Registered commands ({len(cmds)}):")
                for c in cmds:
                    aliases = (
                        f" (aliases: {', '.join(c['aliases'])})" if c["aliases"] else ""
                    )
                    print(f"    /{c['name']} — {c['description']}{aliases}")
            else:
                print("  No commands registered.")
            print()
            continue
        if lowered in ("/events", "events"):
            types = api.event_handler_types
            if types:
                print(f"\n  Subscribed event types ({len(types)}):")
                for t in types:
                    print(f"    {t.__name__}")
            else:
                print("  No event handlers registered.")
            print()
            continue

        # Tool invocation: tool:name {"args": "here"}
        if raw.startswith("tool:"):
            parts = raw[5:].strip().split(maxsplit=1)
            tool_name = parts[0]
            try:
                arguments = json.loads(parts[1]) if len(parts) > 1 else {}
            except json.JSONDecodeError as exc:
                print(f"  \033[31m[Error]\033[0m Invalid JSON: {exc}")
                continue
            result = await api.invoke_tool(tool_name, arguments)
            print(f"  \033[33m[Tool {tool_name}]\033[0m: {result}")
            continue

        # Event dispatch: event:Type {"payload": "json"}
        if raw.startswith("event:"):
            parts = raw[6:].strip().split(maxsplit=1)
            event_type_name = parts[0]

            # Look up the event type by name from subscribed handlers
            matching = [
                t for t in api.event_handler_types if t.__name__ == event_type_name
            ]
            if not matching:
                # Try from SDK events module
                try:
                    from nahida_bot_sdk import events as ev

                    event_cls = getattr(ev, event_type_name, None)
                except AttributeError:
                    event_cls = None
                if event_cls is None:
                    print(
                        f"  \033[31m[Error]\033[0m No handlers for event type '{event_type_name}' and type not found in SDK"
                    )
                    continue
                matching = [event_cls]

            event_cls = matching[0]
            try:
                if len(parts) > 1:
                    payload_data = json.loads(parts[1])
                    event = event_cls(payload=payload_data)
                else:
                    # Try empty constructor
                    event = event_cls(payload={})
            except Exception as exc:
                print(f"  \033[31m[Error]\033[0m Failed to construct event: {exc}")
                continue
            await api._trigger_event(event)
            print(f"  \033[36m[Event]\033[0m Fired {event_type_name}")
            continue

        # Command invocation: /name args
        if raw.startswith("/"):
            cmd_parts = raw[1:].split(maxsplit=1)
            cmd_name = cmd_parts[0]
            args = cmd_parts[1] if len(cmd_parts) > 1 else ""
            result = await api.invoke_command(cmd_name, args)
            print(f"  \033[1mBot\033[0m: {result}")
            continue

        # Plain text — simulate MessageReceived
        msg = MessagePayload(
            message=raw,
            session_id="console:private:test",
        )
        event = MessageReceived(payload=msg)
        await api._trigger_event(event)

        # Show any messages sent by event handlers
        while api.sent_messages:
            target, outbound = api.sent_messages.pop(0)
            print(f"  \033[1mBot\033[0m: {outbound.text}")


def _guess_missing_package(exc: ImportError | ModuleNotFoundError) -> str | None:
    """Try to extract the missing package name from an import error."""
    msg = str(exc)
    if "No module named" in msg:
        name = msg.split("No module named ")[-1].strip("'\"")
        return name.split(".")[0]
    return None


def main() -> None:
    """Entry point: python -m nahida_bot_sdk.testing.console <plugin-dir>"""
    if len(sys.argv) < 2:
        print("Usage: python -m nahida_bot_sdk.testing.console <plugin-dir>")
        sys.exit(1)
    asyncio.run(run_console(sys.argv[1]))


if __name__ == "__main__":
    main()
