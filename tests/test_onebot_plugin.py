"""Tests for OneBot channel plugin behavior."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from nahida_bot.channels.onebot.plugin import OneBotPlugin
from nahida_bot.core.events import MessageReceived
from nahida_bot.plugins.base import OutboundMessage
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
        content = b"content"
        headers = {"content-type": "text/plain"}

        def raise_for_status(self) -> None:
            pass

    class _FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def get(self, url: str) -> _FakeResponse:
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
