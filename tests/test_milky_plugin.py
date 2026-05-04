"""Tests for Milky channel plugin lifecycle and routing."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from nahida_bot.channels.milky.client import MilkyAPIError
from nahida_bot.channels.milky.plugin import MilkyPlugin
from nahida_bot.channels.milky.segments import OutgoingTextSegment
from nahida_bot.plugins.base import Attachment, OutboundMessage
from nahida_bot.plugins.manifest import PluginManifest

from .helpers import RecordingMockBotAPI

pytestmark = pytest.mark.asyncio


def _manifest(**config_overrides: object) -> PluginManifest:
    config: dict[str, Any] = {"base_url": "http://milky.local"}
    config.update(config_overrides)
    return PluginManifest(
        id="milky",
        name="Milky Channel",
        version="0.1.0",
        entrypoint="nahida_bot.channels.milky.plugin:MilkyPlugin",
        config=config,
    )


class _FakeClient:
    def __init__(self) -> None:
        self.closed = False
        self.private_messages: list[tuple[int, list[object]]] = []
        self.group_messages: list[tuple[int, list[object]]] = []
        self.private_files: list[tuple[int, object]] = []
        self.group_files: list[tuple[int, object]] = []
        self.fail_next_group_message = False

    async def get_login_info(self) -> dict[str, object]:
        return {"uin": 999}

    async def get_forwarded_messages(self, forward_id: str) -> list[object]:
        return []

    async def send_private_message(
        self, user_id: int, message: list[object]
    ) -> dict[str, object]:
        self.private_messages.append((user_id, message))
        return {"message_seq": 11}

    async def send_group_message(
        self, group_id: int, message: list[object]
    ) -> dict[str, object]:
        if self.fail_next_group_message:
            self.fail_next_group_message = False
            raise MilkyAPIError(
                "unsupported segment",
                api_name="send_group_message",
                retcode=1404,
            )
        self.group_messages.append((group_id, message))
        return {"message_seq": 22}

    async def upload_private_file(
        self, user_id: int, upload: object
    ) -> dict[str, object]:
        self.private_files.append((user_id, upload))
        return {"file_id": "private-file"}

    async def upload_group_file(
        self, group_id: int, upload: object
    ) -> dict[str, object]:
        self.group_files.append((group_id, upload))
        return {"file_id": "group-file"}

    async def get_resource_temp_url(self, resource_id: str) -> str:
        return f"https://example.com/{resource_id}"

    async def close(self) -> None:
        self.closed = True


async def test_on_load_registers_channel_with_injected_client() -> None:
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    client = _FakeClient()
    plugin._client = client  # type: ignore[assignment]

    await plugin.on_load()

    assert plugin.channel_id == "milky"
    assert api.registered_channels == [plugin]
    assert plugin.self_id == 999


async def test_handle_inbound_event_publishes_message_received() -> None:
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    await plugin.handle_inbound_event(
        {
            "event_type": "message_receive",
            "data": {
                "message_scene": "group",
                "peer_id": 20001,
                "sender_id": 10001,
                "message_seq": 123,
                "time": 1700000000,
                "segments": [
                    {"type": "mention", "data": {"user_id": 999, "name": "bot"}},
                    {"type": "text", "data": {"text": " ping"}},
                ],
            },
        }
    )

    assert len(api.published_events) == 1
    event = api.published_events[0]
    inbound = event.payload.message
    assert inbound.platform == "milky"
    assert inbound.chat_id == "20001"
    assert inbound.text == "ping"
    assert event.payload.session_id == "milky:20001"


async def test_handle_inbound_ignores_non_message_event() -> None:
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())

    await plugin.handle_inbound_event({"event_type": "bot_online", "data": {}})

    assert api.published_events == []


async def test_send_message_routes_to_group_from_scene_memory() -> None:
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    client = _FakeClient()
    plugin._client = client  # type: ignore[assignment]
    await plugin.on_load()
    await plugin.handle_inbound_event(
        {
            "event_type": "message_receive",
            "data": {
                "message_scene": "group",
                "peer_id": 20001,
                "sender_id": 10001,
                "message_seq": 123,
                "time": 1700000000,
                "segments": [
                    {"type": "mention", "data": {"user_id": 999, "name": "bot"}},
                    {"type": "text", "data": {"text": " ping"}},
                ],
            },
        }
    )

    result = await plugin.send_message("20001", OutboundMessage(text="hi"))

    assert result == "22"
    assert len(client.group_messages) == 1
    peer_id, message = client.group_messages[0]
    assert peer_id == 20001
    assert isinstance(message[0], OutgoingTextSegment)


async def test_send_message_routes_explicit_friend_and_uploads_file() -> None:
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    client = _FakeClient()
    plugin._client = client  # type: ignore[assignment]
    await plugin.on_load()

    result = await plugin.send_message(
        "friend:10001",
        OutboundMessage(
            text="",
            attachments=[
                Attachment(
                    type="document",
                    path="file:///tmp/report.pdf",
                    filename="report.pdf",
                )
            ],
        ),
    )

    assert result == "private-file"
    assert len(client.private_files) == 1


async def test_send_message_falls_back_when_rich_segments_unsupported() -> None:
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    client = _FakeClient()
    client.fail_next_group_message = True
    plugin._client = client  # type: ignore[assignment]
    await plugin.on_load()

    result = await plugin.send_message(
        "group:20001",
        OutboundMessage(
            text="",
            extra={
                "milky_forward": {
                    "title": "History",
                    "messages": [
                        {
                            "user_id": 10001,
                            "sender_name": "Alice",
                            "text": "hello",
                        }
                    ],
                }
            },
        ),
    )

    assert result == "22"
    assert len(client.group_messages) == 1
    sent_segments = client.group_messages[0][1]
    assert len(sent_segments) == 1
    assert getattr(sent_segments[0], "text") == "History\n- Alice: hello"


async def test_send_message_invalid_target_returns_empty_id() -> None:
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    client = _FakeClient()
    plugin._client = client  # type: ignore[assignment]
    await plugin.on_load()

    result = await plugin.send_message("not-a-number", OutboundMessage(text="hi"))

    assert result == ""
    assert client.private_messages == []
    assert client.group_messages == []


async def test_scene_cache_is_bounded() -> None:
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest(scene_cache_size=2))
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    for peer_id in (20001, 20002, 20003):
        await plugin.handle_inbound_event(
            {
                "event_type": "message_receive",
                "data": {
                    "message_scene": "group",
                    "peer_id": peer_id,
                    "sender_id": 10001,
                    "message_seq": peer_id,
                    "time": 1700000000,
                    "segments": [
                        {"type": "mention", "data": {"user_id": 999, "name": "bot"}},
                        {"type": "text", "data": {"text": " ping"}},
                    ],
                },
            }
        )

    assert _scene_cache(plugin) == {"20002": "group", "20003": "group"}


async def test_on_enable_starts_stream_and_registers_tool() -> None:
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())

    stream = AsyncMock()
    with patch(
        "nahida_bot.channels.milky.plugin.MilkyEventStream",
        return_value=stream,
    ):
        await plugin.on_enable()

    stream.start.assert_awaited_once()
    assert "milky_get_resource_temp_url" in api.registered_tools
    await plugin.on_disable()


async def test_on_disable_stops_stream_and_closes_client() -> None:
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    client = _FakeClient()
    stream = AsyncMock()
    plugin._client = client  # type: ignore[assignment]
    setattr(plugin, "_event_stream", stream)

    await plugin.on_disable()

    stream.stop.assert_awaited_once()
    assert client.closed is True


def _scene_cache(plugin: MilkyPlugin) -> dict[str, str]:
    return dict(getattr(plugin, "_scene_by_peer"))
