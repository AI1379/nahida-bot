"""Testing utilities for nahida-bot plugin development."""

from __future__ import annotations

from typing import Any, Awaitable, Callable
from unittest.mock import MagicMock

from nahida_bot_sdk.chat_address import ChatAddress
from nahida_bot_sdk.messaging import InboundMessage, OutboundMessage


class MockBotAPI:
    """Minimal no-op BotAPI stub for testing.

    All methods are no-ops.  For stateful tracking (recording calls),
    use ``RecordingMockBotAPI`` instead.
    """

    async def send_message(
        self, target: str, message: Any, *, channel: str = ""
    ) -> str:
        return ""

    async def record_session_event(
        self,
        session_id: str,
        content: str,
        *,
        source: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        pass

    def on_event(self, event_type: type) -> Callable:
        return lambda f: f

    def subscribe(
        self, event_type: type, handler: Callable[..., Awaitable[None]]
    ) -> Any:
        return None

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Callable[..., Awaitable[str]],
    ) -> None:
        pass

    def register_channel(self, channel: Any) -> None:
        pass

    def register_provider_type(
        self,
        type_key: str,
        factory: Any,
        *,
        config_schema: dict[str, Any] | None = None,
        description: str = "",
    ) -> None:
        pass

    def register_command(
        self,
        name: str,
        handler: Callable[..., Awaitable[Any]],
        *,
        description: str = "",
        aliases: list[str] | None = None,
    ) -> None:
        pass

    async def get_session(self, session_id: str) -> Any:
        return None

    async def record_message_delivery(
        self,
        *,
        target: ChatAddress | str,
        text: str,
        source: str,
        delivery_mode: str = "",
        status: str = "sent",
        message_id: str = "",
        error: str = "",
        metadata: dict[str, Any] | None = None,
        source_session_id: str = "",
        source_chat_address: str = "",
        source_user_id: str = "",
    ) -> str:
        return ""

    async def clear_session(self, session_id: str) -> int:
        return 0

    async def start_new_session(self, address: ChatAddress) -> str | None:
        return None

    async def get_session_info(self, session_id: str) -> dict[str, Any]:
        return {}

    def get_session_run_status(self, session_id: str) -> dict[str, Any]:
        return {"active": False, "state": "idle", "pending_messages": 0}

    def list_commands(self) -> list[Any]:
        return []

    def list_models(self) -> list[dict[str, str]]:
        return []

    async def set_session_model(self, session_id: str, model_name: str) -> str | None:
        return None

    async def update_runtime_settings(
        self, session_id: str, updates: dict[str, Any]
    ) -> dict[str, Any]:
        return dict(updates)

    async def memory_search(self, query: str, *, limit: int = 5) -> list[Any]:
        return []

    @property
    def scheduler_service(self) -> Any | None:
        return None

    async def memory_store(
        self, key: str, content: str, *, metadata: dict[str, Any] | None = None
    ) -> None:
        pass

    async def workspace_read(self, path: str) -> str:
        return ""

    async def workspace_write(self, path: str, content: str) -> None:
        pass

    def resolve_workspace_path(self, path: str) -> str:
        return path

    async def publish_event(self, event: Any) -> None:
        pass

    @property
    def logger(self) -> Any:
        return MagicMock()


class RecordingMockBotAPI(MockBotAPI):
    """Stateful BotAPI mock that records calls for assertion.

    Tracks: published events, registered tools, registered channels.
    """

    def __init__(self) -> None:
        self.published_events: list[Any] = []
        self.registered_tools: dict[str, dict[str, Any]] = {}
        self.registered_channels: list[Any] = []

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Any,
    ) -> None:
        self.registered_tools[name] = {
            "description": description,
            "parameters": parameters,
            "handler": handler,
        }

    def register_channel(self, channel: Any) -> None:
        self.registered_channels.append(channel)

    async def publish_event(self, event: Any) -> None:
        self.published_events.append(event)


class StubChannelService:
    """Plain object satisfying the ChannelService protocol (not a Plugin).

    Use when you only need a channel-shaped object without Plugin machinery.
    For tests that need a real Plugin, extend Plugin directly in the test.
    """

    def __init__(self, channel_id: str = "test.channel") -> None:
        self._channel_id = channel_id

    @property
    def channel_id(self) -> str:
        return self._channel_id

    async def handle_inbound_event(self, event: dict[str, Any]) -> None:
        pass

    async def send_message(
        self,
        target: str,
        message: Any,  # OutboundMessage
    ) -> str:
        return "msg_1"


class ConsoleMockBotAPI:
    """Interactive-capable BotAPI mock for the plugin testing console.

    Tracks all registrations and supports dispatching commands, tools,
    and events interactively.  Also records sent messages so the console
    can print responses.
    """

    def __init__(self) -> None:
        self.sent_messages: list[tuple[str, OutboundMessage]] = []
        self._tools: dict[str, dict[str, Any]] = {}
        self._commands: dict[str, dict[str, Any]] = {}
        self._event_handlers: dict[type, list[Callable[..., Awaitable[None]]]] = {}
        self._workspace: dict[str, str] = {}

    # ── Messaging ──────────────────────────────────────

    async def send_message(
        self, target: str, message: OutboundMessage, *, channel: str = ""
    ) -> str:
        self.sent_messages.append((target, message))
        return f"msg_{len(self.sent_messages)}"

    async def record_session_event(
        self,
        session_id: str,
        content: str,
        *,
        source: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        pass

    async def record_message_delivery(self, **kw: Any) -> str:
        return ""

    # ── Event System ───────────────────────────────────

    def on_event(self, event_type: type) -> Callable:
        def decorator(handler: Callable[..., Awaitable[None]]) -> Callable:
            self.subscribe(event_type, handler)
            return handler

        return decorator

    def subscribe(
        self,
        event_type: type,
        handler: Callable[..., Awaitable[None]],
    ) -> _MockSubHandle:
        handlers = self._event_handlers.setdefault(event_type, [])
        handlers.append(handler)
        return _MockSubHandle(handlers, handler)

    async def _trigger_event(self, event: Any) -> None:
        """Fire an event to all registered handlers for that event type."""
        for handler in self._event_handlers.get(type(event), []):
            await handler(event)

    # ── Tool Registration ──────────────────────────────

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Callable[..., Awaitable[str]],
    ) -> None:
        self._tools[name] = {
            "description": description,
            "parameters": parameters,
            "handler": handler,
        }

    def list_tools(self) -> list[str]:
        return list(self._tools)

    async def invoke_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Invoke a registered tool by name with JSON-decoded arguments."""
        entry = self._tools.get(name)
        if entry is None:
            return (
                f"[Error] Tool '{name}' not found. Available: {', '.join(self._tools)}"
            )
        try:
            return await entry["handler"](**arguments)
        except Exception as exc:
            return f"[Error] Tool '{name}' raised {type(exc).__name__}: {exc}"

    # ── Command Registration ───────────────────────────

    def register_command(
        self,
        name: str,
        handler: Callable[..., Awaitable[Any]],
        *,
        description: str = "",
        aliases: list[str] | None = None,
    ) -> None:
        self._commands[name] = {
            "handler": handler,
            "description": description,
            "aliases": aliases or [],
        }
        for alias in aliases or []:
            self._commands[alias] = self._commands[name]

    def list_commands(self) -> list[dict[str, Any]]:
        seen: set[int] = set()
        result: list[dict[str, Any]] = []
        for name, entry in self._commands.items():
            hid = id(entry)
            if hid not in seen:
                seen.add(hid)
                result.append(
                    {
                        "name": name,
                        "description": entry["description"],
                        "aliases": entry["aliases"],
                    }
                )
        return result

    async def invoke_command(self, name: str, args: str = "") -> str:
        """Dispatch a command by name and return the result text."""
        entry = self._commands.get(name)
        if entry is None:
            return f"[Error] Command '{name}' not found."
        handler = entry["handler"]
        try:
            result = await handler(
                session_id="console:private:test",
                address="console:private:test",
                message=InboundMessage(
                    message_id="console_0",
                    platform="console",
                    chat_id="test",
                    user_id="console_user",
                    text=f"/{name} {args}",
                    raw_event={},
                ),
            )
        except Exception as exc:
            return f"[Error] Command '{name}' raised {type(exc).__name__}: {exc}"

        return _format_command_result(result)

    # ── Service Registration ──────────────────────────

    def register_channel(self, channel: Any) -> None:
        pass

    def register_provider_type(self, *args: Any, **kw: Any) -> None:
        pass

    @property
    def scheduler_service(self) -> Any | None:
        return None

    # ── Session ────────────────────────────────────────

    async def get_session(self, session_id: str) -> Any:
        return None

    async def clear_session(self, session_id: str) -> int:
        return 0

    async def start_new_session(self, address: ChatAddress) -> str | None:
        return "console:private:test:new"

    async def get_session_info(self, session_id: str) -> dict[str, Any]:
        return {}

    def get_session_run_status(self, session_id: str) -> dict[str, Any]:
        return {"active": False, "state": "idle", "pending_messages": 0}

    def list_models(self) -> list[dict[str, str]]:
        return []

    async def set_session_model(self, session_id: str, model_name: str) -> str | None:
        return None

    async def update_runtime_settings(
        self, session_id: str, updates: dict[str, Any]
    ) -> dict[str, Any]:
        return dict(updates)

    # ── Memory ─────────────────────────────────────────

    async def memory_search(self, query: str, *, limit: int = 5) -> list[Any]:
        return []

    async def memory_store(
        self, key: str, content: str, *, metadata: dict[str, Any] | None = None
    ) -> None:
        pass

    # ── Workspace ──────────────────────────────────────

    async def workspace_read(self, path: str) -> str:
        return self._workspace.get(path, "")

    async def workspace_write(self, path: str, content: str) -> None:
        self._workspace[path] = content

    def resolve_workspace_path(self, path: str) -> str:
        return path

    # ── Event Publishing ───────────────────────────────

    async def publish_event(self, event: Any) -> None:
        pass

    @property
    def logger(self) -> Any:
        return MagicMock()

    # ── Console helpers ───────────────────────────────

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools)

    @property
    def event_handler_types(self) -> list[type]:
        return list(self._event_handlers)


class _MockSubHandle:
    def __init__(
        self,
        handlers: list[Callable[..., Awaitable[None]]],
        handler: Callable[..., Awaitable[None]],
    ) -> None:
        self._handlers = handlers
        self._handler = handler

    def unsubscribe(self) -> None:
        if self._handler in self._handlers:
            self._handlers.remove(self._handler)


def _format_command_result(result: Any) -> str:
    """Convert a CommandHandlerResult to a display string."""
    if result is None:
        return "(no response)"
    if isinstance(result, str):
        return result

    from nahida_bot_sdk.commands import CommandResult

    if isinstance(result, CommandResult):
        if result.suppress_response:
            return "(suppressed)"
        if result.message:
            return result.message.text
        return "(empty result)"
    if isinstance(result, OutboundMessage):
        return result.text
    return str(result)
