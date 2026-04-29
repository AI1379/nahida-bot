"""Tests for ChannelPlugin base class."""

from typing import Any, Awaitable, Callable
from unittest.mock import MagicMock

import pytest

from nahida_bot.plugins.base import InboundMessage, OutboundMessage, Plugin
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


class _ConcreteChannel(ChannelPlugin):
    """Concrete subclass so we can test ChannelPlugin behaviour."""

    async def on_load(self) -> None:
        pass


class TestChannelPluginInterface:
    def test_channel_plugin_is_plugin_subclass(self) -> None:
        assert issubclass(ChannelPlugin, Plugin)

    def test_channel_id_equals_plugin_id(self) -> None:
        manifest = _make_manifest(id="my.telegram")
        plugin = _ConcreteChannel(api=_MockAPI(), manifest=manifest)
        assert plugin.channel_id == "my.telegram"

    def test_default_communication_flags_are_false(self) -> None:
        manifest = _make_manifest()
        plugin = _ConcreteChannel(api=_MockAPI(), manifest=manifest)
        assert plugin.SUPPORT_HTTP_SERVER is False
        assert plugin.SUPPORT_HTTP_CLIENT is False
        assert plugin.SUPPORT_WEBSOCKET_SERVER is False
        assert plugin.SUPPORT_WEBSOCKET_CLIENT is False
        assert plugin.SUPPORT_SSE is False

    async def test_handle_inbound_event_raises(self) -> None:
        manifest = _make_manifest()
        plugin = _ConcreteChannel(api=_MockAPI(), manifest=manifest)
        with pytest.raises(NotImplementedError):
            await plugin.handle_inbound_event({})

    async def test_send_message_raises(self) -> None:
        manifest = _make_manifest()
        plugin = _ConcreteChannel(api=_MockAPI(), manifest=manifest)
        with pytest.raises(NotImplementedError):
            await plugin.send_message("user_1", OutboundMessage(text="hi"))

    async def test_get_user_info_returns_empty(self) -> None:
        manifest = _make_manifest()
        plugin = _ConcreteChannel(api=_MockAPI(), manifest=manifest)
        result = await plugin.get_user_info("user_1")
        assert result == {}

    async def test_get_group_info_returns_empty(self) -> None:
        manifest = _make_manifest()
        plugin = _ConcreteChannel(api=_MockAPI(), manifest=manifest)
        result = await plugin.get_group_info("group_1")
        assert result == {}

    def test_manifest_type_channel(self) -> None:
        manifest = _make_manifest()
        assert manifest.type == "channel"


class TestInboundMessageFields:
    def test_default_command_prefix(self) -> None:
        msg = InboundMessage(
            message_id="1",
            platform="test",
            chat_id="c1",
            user_id="u1",
            text="/help",
            raw_event={},
        )
        assert msg.command_prefix == "/"

    def test_custom_command_prefix(self) -> None:
        msg = InboundMessage(
            message_id="1",
            platform="test",
            chat_id="c1",
            user_id="u1",
            text="!help",
            raw_event={},
            command_prefix="!",
        )
        assert msg.command_prefix == "!"
