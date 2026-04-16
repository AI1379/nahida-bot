"""Tests for ChannelRegistry."""

from nahida_bot.core.channel_registry import ChannelRegistry
from nahida_bot.plugins.base import OutboundMessage
from nahida_bot.plugins.channel_plugin import ChannelPlugin
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
    return PluginManifest(**defaults)


class _MockAPI:
    pass


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
