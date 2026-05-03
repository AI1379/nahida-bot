"""Tests for ChannelRegistry."""

from typing import Any

from nahida_bot.core.channel_registry import ChannelRegistry
from nahida_bot.plugins.base import OutboundMessage, Plugin
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


class _StubChannel(Plugin):
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


class TestChannelRegistry:
    def test_register_and_get(self) -> None:
        reg = ChannelRegistry()
        ch = _StubChannel(api=MockBotAPI(), manifest=_make_manifest(id="telegram"))
        reg.register(ch)
        assert reg.get("telegram") is ch

    def test_get_nonexistent_returns_none(self) -> None:
        reg = ChannelRegistry()
        assert reg.get("telegram") is None

    def test_unregister_removes_channel(self) -> None:
        reg = ChannelRegistry()
        ch = _StubChannel(api=MockBotAPI(), manifest=_make_manifest(id="telegram"))
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
        ch1 = _StubChannel(api=MockBotAPI(), manifest=m1)
        ch2 = _StubChannel(api=MockBotAPI(), manifest=m2)
        reg.register(ch1)
        reg.register(ch2)
        assert reg.get("telegram") is ch2
