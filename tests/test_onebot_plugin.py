"""Tests for OneBot channel plugin behavior."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from nahida_bot.channels.onebot.plugin import OneBotPlugin
from nahida_bot.core.events import MessageReceived
from nahida_bot.plugins.base import InboundAttachment, OutboundMessage
from nahida_bot.plugins.manifest import PluginManifest

from .helpers import RecordingMockBotAPI

pytestmark = pytest.mark.asyncio


def _manifest(**config_overrides: object) -> PluginManifest:
    config: dict[str, Any] = {"ws_url": "ws://127.0.0.1:3001"}
    config.update(config_overrides)
    return PluginManifest(
        id="onebot",
        name="OneBot Channel",
        version="0.1.0",
        entrypoint="nahida_bot.channels.onebot.plugin:OneBotPlugin",
        config=config,
    )


class _FakeConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_action(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((action, params))
        return {"status": "ok", "retcode": 0, "data": {"message_id": 123}}


async def test_handle_v11_cq_fallback_group_mention_publishes_received() -> None:
    api = RecordingMockBotAPI()
    plugin = OneBotPlugin(api=api, manifest=_manifest())

    await plugin.handle_inbound_event(
        {
            "post_type": "message",
            "message_type": "group",
            "sub_type": "normal",
            "message_id": 123,
            "group_id": 20001,
            "user_id": 10001,
            "self_id": 999,
            "time": 1700000000,
            "message": "[CQ:at,qq=999] ping",
            "raw_message": "[CQ:at,qq=999] ping",
            "sender": {"nickname": "Alice", "role": "member"},
        }
    )

    assert len(api.published_events) == 1
    event = api.published_events[0]
    assert isinstance(event, MessageReceived)
    assert event.payload.session_id == "onebot:group:20001"
    inbound = event.payload.message
    assert inbound.text == "ping"
    assert inbound.mentions_bot is True
    assert inbound.mentioned_user_ids == ("999",)


async def test_handle_v11_message_string_group_mention_publishes_received() -> None:
    api = RecordingMockBotAPI()
    plugin = OneBotPlugin(api=api, manifest=_manifest())

    await plugin.handle_inbound_event(
        {
            "post_type": "message",
            "message_type": "group",
            "sub_type": "normal",
            "message_id": 123,
            "group_id": 20001,
            "user_id": 10001,
            "self_id": 999,
            "time": 1700000000,
            "message": "[CQ:at,qq=999] ping",
            "sender": {"nickname": "Alice", "role": "member"},
        }
    )

    assert len(api.published_events) == 1
    event = api.published_events[0]
    assert isinstance(event, MessageReceived)
    inbound = event.payload.message
    assert inbound.text == "ping"
    assert inbound.mentions_bot is True
    assert inbound.mentioned_user_ids == ("999",)


async def test_send_message_uses_chat_address_for_group_target() -> None:
    api = RecordingMockBotAPI()
    plugin = OneBotPlugin(api=api, manifest=_manifest())
    conn = _FakeConnection()
    plugin._connection = conn  # type: ignore[assignment]

    result = await plugin.send_message(
        "20001",
        OutboundMessage(
            text="hi",
            extra={"chat_address": "onebot:group:20001"},
        ),
    )

    assert result == "123"
    assert conn.calls == [
        (
            "send_msg",
            {
                "message_type": "group",
                "message": [{"type": "text", "data": {"text": "hi"}}],
                "group_id": 20001,
            },
        )
    ]


async def test_send_message_invalid_target_returns_empty_id() -> None:
    api = RecordingMockBotAPI()
    plugin = OneBotPlugin(api=api, manifest=_manifest())
    conn = _FakeConnection()
    plugin._connection = conn  # type: ignore[assignment]

    result = await plugin.send_message(
        "thread:abc",
        OutboundMessage(text="hi"),
    )

    assert result == ""
    assert conn.calls == []


async def test_download_media_sanitizes_file_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeResponse:
        headers = {"content-type": "text/plain"}

        async def __aenter__(self) -> "_FakeResponse":
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        def raise_for_status(self) -> None:
            pass

        async def aiter_bytes(self):
            yield b"content"

    class _FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        def stream(self, method: str, url: str) -> _FakeResponse:
            return _FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    api = RecordingMockBotAPI()
    plugin = OneBotPlugin(
        api=api,
        manifest=_manifest(media_download_dir=str(tmp_path)),
    )

    result = await plugin.download_media(
        "https://example.test/file.txt",
        file_name="../escape.txt",
    )

    assert result is not None
    assert Path(result.path) == tmp_path / "escape.txt"
    assert (tmp_path / "escape.txt").read_bytes() == b"content"
    assert not (tmp_path.parent / "escape.txt").exists()


async def test_download_media_uses_shared_cache_and_dedups(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Downloads route through the shared MediaCache and dedup on repeat (#45)."""
    from nahida_bot.agent.media.cache import MediaCache
    from nahida_bot.agent.media.store import MediaStore

    fetch_count = 0

    class _FakeResponse:
        def __init__(self) -> None:
            self.headers = {"content-type": "image/png"}

        async def __aenter__(self) -> "_FakeResponse":
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        def raise_for_status(self) -> None:
            pass

        async def aiter_bytes(self):
            yield b"onebot-bytes"

    class _FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        def stream(self, method: str, url: str) -> _FakeResponse:
            nonlocal fetch_count
            fetch_count += 1
            return _FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    cache = MediaCache(tmp_path / "media_cache", ttl_seconds=3600)
    await cache.ensure_dir()
    api = RecordingMockBotAPI()
    api.get_media_store = lambda: MediaStore(cache)  # type: ignore[attr-defined]
    plugin = OneBotPlugin(
        api=api,
        manifest=_manifest(media_download_dir=str(tmp_path / "legacy")),
    )

    first = await plugin.download_media(
        "https://example.test/file.png", file_name="file.png"
    )
    assert first is not None
    assert str(tmp_path / "media_cache") in first.path
    assert Path(first.path).read_bytes() == b"onebot-bytes"
    assert first.mime_type == "image/png"
    assert fetch_count == 1

    # Second call is a cache hit: no second network fetch.
    second = await plugin.download_media(
        "https://example.test/file.png", file_name="file.png"
    )
    assert second is not None
    assert second.path == first.path
    assert fetch_count == 1
    # Legacy dir is untouched because the cache is configured.
    assert not (tmp_path / "legacy").exists() or not any(
        (tmp_path / "legacy").iterdir()
    )


async def test_inbound_media_attaches_shared_cache_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nahida_bot.agent.media.cache import MediaCache
    from nahida_bot.agent.media.store import MediaStore

    fetch_count = 0

    class _FakeResponse:
        headers = {"content-length": "12", "content-type": "image/png"}

        async def __aenter__(self) -> "_FakeResponse":
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        def raise_for_status(self) -> None:
            pass

        async def aiter_bytes(self):
            yield b"inbound-data"

    class _FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        def stream(self, method: str, url: str) -> _FakeResponse:
            nonlocal fetch_count
            fetch_count += 1
            return _FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    cache = MediaCache(tmp_path / "media_cache", ttl_seconds=3600)
    api = RecordingMockBotAPI()
    api.get_media_store = lambda: MediaStore(cache)  # type: ignore[attr-defined]
    plugin = OneBotPlugin(api=api, manifest=_manifest())
    attachment = InboundAttachment(
        kind="image",
        platform_id="file-1",
        url="https://example.test/image.png",
    )

    first = await plugin._cache_inbound_media([attachment])
    second = await plugin._cache_inbound_media([attachment])

    assert first[0].path
    assert first[0].path == second[0].path
    assert Path(first[0].path).read_bytes() == b"inbound-data"
    assert fetch_count == 1
