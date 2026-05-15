"""Shared test helpers for plugin tests."""

from typing import Any, Awaitable, Callable
from unittest.mock import MagicMock


class MockBotAPI:
    """Minimal no-op BotAPI stub for testing.

    All methods are no-ops.  For stateful tracking (recording calls),
    use ``RecordingMockBotAPI`` instead.
    """

    async def send_message(
        self, target: str, message: Any, *, channel: str = ""
    ) -> str:
        return ""

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

    async def clear_session(self, session_id: str) -> int:
        return 0

    async def start_new_session(self, platform: str, chat_id: str) -> str | None:
        return None

    async def get_session_info(self, session_id: str) -> dict[str, Any]:
        return {}

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
