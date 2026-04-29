"""Tests for ChannelRegistry."""

from typing import Any, Awaitable, Callable
from unittest.mock import MagicMock

from nahida_bot.core.channel_registry import ChannelRegistry
from nahida_bot.plugins.base import OutboundMessage
from nahida_bot.plugins.channel_plugin import ChannelPlugin
from nahida_bot.plugins.commands import CommandHandlerResult
from nahida_bot.plugins.manifest import PluginManifest


def _make_manifest(**overrides: object) -> PluginManifest:
    defaults = {
        "id": "test.channel",
        "name": "Test Channel",
        "version": "1.0.0",
        "entrypoint": "test:TestChannel",
        "type": "channel",
    }
    defaults.update(overrides)  # type: ignore[typeddict-item]
    return PluginManifest(**defaults)  # type: ignore[arg-type]


class _MockAPI:
    """Minimal BotAPI stub satisfying the BotAPI protocol for testing."""

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

    def register_command(
        self,
        name: str,
        handler: Callable[..., Awaitable[CommandHandlerResult]],
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

    async def memory_search(self, query: str, *, limit: int = 5) -> list[Any]:
        return []

    async def memory_store(
        self, key: str, content: str, *, metadata: dict[str, Any] | None = None
    ) -> None:
        pass

    async def workspace_read(self, path: str) -> str:
        return ""

    async def workspace_write(self, path: str, content: str) -> None:
        pass

    async def publish_event(self, event: Any) -> None:
        pass

    @property
    def logger(self) -> Any:
        return MagicMock()


class _StubChannel(ChannelPlugin):
    async def on_load(self) -> None:
        pass

    async def handle_inbound_event(self, event: dict) -> None:
        pass

    async def send_message(self, target: str, message: OutboundMessage) -> str:
        return "msg_1"


class TestChannelRegistry:
    def test_register_and_get(self) -> None:
        reg = ChannelRegistry()
        ch = _StubChannel(api=_MockAPI(), manifest=_make_manifest(id="telegram"))
        reg.register(ch)
        assert reg.get("telegram") is ch

    def test_get_nonexistent_returns_none(self) -> None:
        reg = ChannelRegistry()
        assert reg.get("telegram") is None

    def test_unregister_removes_channel(self) -> None:
        reg = ChannelRegistry()
        ch = _StubChannel(api=_MockAPI(), manifest=_make_manifest(id="telegram"))
        reg.register(ch)
        reg.unregister("telegram")
        assert reg.get("telegram") is None

    def test_unregister_nonexistent_is_noop(self) -> None:
        reg = ChannelRegistry()
        reg.unregister("nope")  # should not raise

    def test_register_overwrites_previous(self) -> None:
        reg = ChannelRegistry()
        m1 = _make_manifest(id="telegram")
        m2 = _make_manifest(id="telegram")
        ch1 = _StubChannel(api=_MockAPI(), manifest=m1)
        ch2 = _StubChannel(api=_MockAPI(), manifest=m2)
        reg.register(ch1)
        reg.register(ch2)
        assert reg.get("telegram") is ch2
