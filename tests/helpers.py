"""Shared test helpers for plugin tests."""

from typing import Any, Awaitable, Callable
from unittest.mock import MagicMock

from nahida_bot.core.chat_address import ChatAddress


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
        *,
        requires_admin: bool = False,
    ) -> None:
        pass

    def unregister_tool(self, name: str) -> bool:
        return False

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

    def register_webhook_endpoint(
        self,
        path: str,
        handler: Callable[..., Awaitable[Any]],
        *,
        methods: tuple[str, ...] = ("POST",),
    ) -> Any:
        return _MockWebhookHandle()

    def register_prompt_supplement(
        self,
        key: str,
        instruction: str,
        *,
        channel: str | None = None,
        filter: Callable[..., bool] | None = None,
    ) -> None:
        pass

    def unregister_prompt_supplement(self, key: str) -> bool:
        return False

    def register_command(
        self,
        name: str,
        handler: Callable[..., Awaitable[Any]],
        *,
        description: str = "",
        aliases: list[str] | None = None,
        arguments: list[Any] | None = None,
    ) -> None:
        pass

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

    async def complete_command_argument(self, query: Any) -> list[Any]:
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

    # ── Plugin Data Store ─────────────────────────────

    async def plugin_data_get(self, key: str) -> Any | None:
        return None

    async def plugin_data_set(self, key: str, value: Any) -> None:
        pass

    async def plugin_data_delete(self, key: str) -> bool:
        return False

    async def plugin_data_list(self, prefix: str = "") -> dict[str, Any]:
        return {}

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
        self.registered_webhooks: dict[str, dict[str, Any]] = {}
        self.registered_prompt_supplements: dict[str, dict[str, Any]] = {}
        self.registered_commands: dict[str, dict[str, Any]] = {}
        self.command_infos: list[Any] = []
        self.completion_results: list[Any] = []
        self._plugin_data: dict[str, Any] = {}

    def list_commands(self) -> list[Any]:
        return list(self.command_infos)

    def register_command(
        self,
        name: str,
        handler: Callable[..., Awaitable[Any]],
        *,
        description: str = "",
        aliases: list[str] | None = None,
        arguments: list[Any] | None = None,
    ) -> None:
        self.registered_commands[name] = {
            "handler": handler,
            "description": description,
            "aliases": aliases or [],
            "arguments": list(arguments or []),
        }

    async def complete_command_argument(self, query: Any) -> list[Any]:
        return list(self.completion_results)

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Any,
        *,
        requires_admin: bool = False,
    ) -> None:
        self.registered_tools[name] = {
            "description": description,
            "parameters": parameters,
            "handler": handler,
            "requires_admin": requires_admin,
        }

    def unregister_tool(self, name: str) -> bool:
        return self.registered_tools.pop(name, None) is not None

    def register_channel(self, channel: Any) -> None:
        self.registered_channels.append(channel)

    def register_webhook_endpoint(
        self,
        path: str,
        handler: Callable[..., Awaitable[Any]],
        *,
        methods: tuple[str, ...] = ("POST",),
    ) -> Any:
        if path in self.registered_webhooks:
            raise KeyError(f"Webhook endpoint '{path}' is already registered")
        self.registered_webhooks[path] = {
            "handler": handler,
            "methods": tuple(methods),
        }
        return _MockWebhookHandle(lambda: self.registered_webhooks.pop(path, None))

    def register_prompt_supplement(
        self,
        key: str,
        instruction: str,
        *,
        channel: str | None = None,
        filter: Callable[..., bool] | None = None,
    ) -> None:
        self.registered_prompt_supplements[key] = {
            "instruction": instruction,
            "channel": channel,
            "filter": filter,
        }

    def unregister_prompt_supplement(self, key: str) -> bool:
        return self.registered_prompt_supplements.pop(key, None) is not None

    async def publish_event(self, event: Any) -> None:
        self.published_events.append(event)

    async def plugin_data_get(self, key: str) -> Any | None:
        return self._plugin_data.get(key)

    async def plugin_data_set(self, key: str, value: Any) -> None:
        self._plugin_data[key] = value

    async def plugin_data_delete(self, key: str) -> bool:
        return self._plugin_data.pop(key, _SENTINEL) is not _SENTINEL

    async def plugin_data_list(self, prefix: str = "") -> dict[str, Any]:
        if prefix:
            return {k: v for k, v in self._plugin_data.items() if k.startswith(prefix)}
        return dict(self._plugin_data)


_SENTINEL = object()


class _MockWebhookHandle:
    def __init__(self, unsubscribe: Callable[[], None] | None = None) -> None:
        self._unsubscribe = unsubscribe
        self.unsubscribed = False

    def unsubscribe(self) -> None:
        if self.unsubscribed:
            return
        self.unsubscribed = True
        if self._unsubscribe is not None:
            self._unsubscribe()


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
