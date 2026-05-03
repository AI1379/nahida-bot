"""Tests for the ChannelService runtime protocol."""

from typing import Any

from nahida_bot.core.channel_registry import ChannelRegistry
from nahida_bot.plugins.base import (
    ChannelService,
    InboundMessage,
    OutboundMessage,
    Plugin,
)
from nahida_bot.plugins.manifest import PluginManifest

from .helpers import MockBotAPI


def _make_manifest(**overrides: object) -> PluginManifest:
    defaults = {
        "id": "test.channel",
        "name": "Test Channel",
        "version": "1.0.0",
        "entrypoint": "test:TestChannel",
    }
    defaults.update(overrides)  # type: ignore[typeddict-item]
    return PluginManifest(**defaults)  # type: ignore[arg-type]


class _ChannelServicePlugin(Plugin):
    """Ordinary plugin that also satisfies the ChannelService protocol."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._channel_id = self.manifest.id

    @property
    def channel_id(self) -> str:
        return self._channel_id

    async def on_load(self) -> None:
        pass

    async def handle_inbound_event(self, event: dict[str, Any]) -> None:
        pass

    async def send_message(self, target: str, message: OutboundMessage) -> str:
        return "msg_1"


class TestChannelServiceProtocol:
    def test_ordinary_plugin_can_satisfy_channel_service(self) -> None:
        plugin = _ChannelServicePlugin(api=MockBotAPI(), manifest=_make_manifest())
        assert isinstance(plugin, Plugin)
        assert isinstance(plugin, ChannelService)

    def test_channel_registry_accepts_protocol_implementation(self) -> None:
        registry = ChannelRegistry()
        plugin = _ChannelServicePlugin(
            api=MockBotAPI(), manifest=_make_manifest(id="telegram")
        )
        registry.register(plugin)
        assert registry.get("telegram") is plugin

    def test_optional_helpers_are_not_required_by_protocol(self) -> None:
        plugin = _ChannelServicePlugin(api=MockBotAPI(), manifest=_make_manifest())
        assert not hasattr(plugin, "get_user_info")
        assert not hasattr(plugin, "get_group_info")


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
