"""Tests for Milky channel plugin lifecycle and routing."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from nahida_bot.channels.milky.client import MilkyAPIError, MilkyNetworkError
from nahida_bot.channels.milky.plugin import MilkyPlugin
from nahida_bot.channels.milky.segments import OutgoingTextSegment
from nahida_bot.core.events import MessageObserved, MessageReceived
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


class _FlakyLoginClient(_FakeClient):
    def __init__(self, *, failures: int, uin: int = 999) -> None:
        super().__init__()
        self.failures = failures
        self.uin = uin
        self.login_calls = 0

    async def get_login_info(self) -> dict[str, object]:
        self.login_calls += 1
        if self.login_calls <= self.failures:
            raise MilkyNetworkError(
                "milky unavailable",
                api_name="get_login_info",
                retryable=True,
            )
        return {"uin": self.uin}


async def test_on_load_registers_channel_with_injected_client() -> None:
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    client = _FakeClient()
    plugin._client = client  # type: ignore[assignment]

    await plugin.on_load()

    assert plugin.channel_id == "milky"
    assert api.registered_channels == [plugin]
    assert plugin.self_id == 999


async def test_on_load_registers_channel_when_login_info_unavailable() -> None:
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    client = _FlakyLoginClient(failures=10)
    plugin._client = client  # type: ignore[assignment]

    await plugin.on_load()

    assert api.registered_channels == [plugin]
    assert plugin.self_id == 0
    assert client.login_calls == 1


async def test_group_mention_drop_logs_when_self_id_unknown() -> None:
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    client = _FlakyLoginClient(failures=10)
    plugin._client = client  # type: ignore[assignment]
    await plugin.on_load()

    with patch("nahida_bot.channels.milky.plugin.logger.warning") as warning:
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

    assert api.published_events == []
    assert any(
        call.args and call.args[0] == "milky.group_message_dropped_self_id_unknown"
        for call in warning.call_args_list
    )


async def test_on_enable_retries_login_info_until_available() -> None:
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(
        api=api,
        manifest=_manifest(
            enable_media_download_tool=False,
            reconnect_initial_delay=0.001,
            reconnect_max_delay=0.001,
        ),
    )
    client = _FlakyLoginClient(failures=1, uin=123456)
    plugin._client = client  # type: ignore[assignment]

    await plugin.on_load()
    assert plugin.self_id == 0

    with patch(
        "nahida_bot.channels.milky.plugin.MilkyEventStream",
        return_value=AsyncMock(),
    ):
        await plugin.on_enable()

    for _ in range(50):
        if plugin.self_id == 123456:
            break
        await asyncio.sleep(0.01)

    assert plugin.self_id == 123456
    assert client.login_calls >= 2
    await plugin.on_disable()


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
    assert isinstance(event, MessageReceived)
    inbound = event.payload.message
    assert inbound.platform == "milky"
    assert inbound.chat_id == "20001"
    assert inbound.text == "ping"
    assert event.payload.session_id == "milky:group:20001"


async def test_handle_inbound_event_observes_untriggered_group_context() -> None:
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(
        api=api,
        manifest=_manifest(group_context_capture=True, group_trigger_mode="mention"),
    )
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
                "segments": [{"type": "text", "data": {"text": "nearby chat"}}],
            },
        }
    )

    assert len(api.published_events) == 1
    event = api.published_events[0]
    assert isinstance(event, MessageObserved)
    inbound = event.payload.message
    assert inbound.text == "nearby chat"
    assert inbound.mentions_bot is False


async def test_handle_friend_file_upload_publishes_message_received() -> None:
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    await plugin.handle_inbound_event(
        {
            "event_type": "friend_file_upload",
            "data": {
                "user_id": 10001,
                "time": 1700000000,
                "file_id": "file-1",
                "file_name": "report.pdf",
                "file_size": 1024,
                "file_hash": "abc",
            },
        }
    )

    assert len(api.published_events) == 1
    event = api.published_events[0]
    assert isinstance(event, MessageReceived)
    assert event.payload.session_id == "milky:private:10001"
    inbound = event.payload.message
    assert inbound.text == "[File: name=report.pdf, file_id=file-1, size=1024]"
    assert len(inbound.attachments) == 1
    attachment = inbound.attachments[0]
    assert attachment.kind == "file"
    assert attachment.platform_id == "file-1"
    assert attachment.file_size == 1024
    assert attachment.metadata["file_name"] == "report.pdf"
    assert attachment.metadata["file_hash"] == "abc"


async def test_handle_group_file_upload_observes_when_capture_enabled() -> None:
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(
        api=api,
        manifest=_manifest(group_context_capture=True, group_trigger_mode="mention"),
    )
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    await plugin.handle_inbound_event(
        {
            "event_type": "group_file_upload",
            "data": {
                "group_id": 20001,
                "user_id": 10001,
                "time": 1700000000,
                "file": {
                    "file_id": "group-file-1",
                    "file_name": "slides.pptx",
                    "file_size": 2048,
                },
            },
        }
    )

    assert len(api.published_events) == 1
    event = api.published_events[0]
    assert isinstance(event, MessageObserved)
    assert event.payload.session_id == "milky:group:20001"
    inbound = event.payload.message
    assert inbound.is_group is True
    assert inbound.text == "[File: name=slides.pptx, file_id=group-file-1, size=2048]"
    assert inbound.attachments[0].metadata["group_id"] == "20001"


async def test_handle_group_file_upload_responds_in_always_mode() -> None:
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest(group_trigger_mode="always"))
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    await plugin.handle_inbound_event(
        {
            "event_type": "group_file_upload",
            "data": {
                "group_id": 20001,
                "user_id": 10001,
                "file_id": "group-file-1",
                "file_name": "slides.pptx",
            },
        }
    )

    assert isinstance(api.published_events[0], MessageReceived)


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


async def test_reenable_recreates_client_after_disable() -> None:
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(
        api=api,
        manifest=_manifest(enable_media_download_tool=False),
    )
    first_client = _FakeClient()
    plugin._client = first_client  # type: ignore[assignment]

    await plugin.on_load()
    with patch(
        "nahida_bot.channels.milky.plugin.MilkyEventStream",
        return_value=AsyncMock(),
    ):
        await plugin.on_enable()
    await plugin.on_disable()

    second_client = _FakeClient()
    with (
        patch(
            "nahida_bot.channels.milky.plugin.MilkyClient",
            return_value=second_client,
        ),
        patch(
            "nahida_bot.channels.milky.plugin.MilkyEventStream",
            return_value=AsyncMock(),
        ),
    ):
        await plugin.on_enable()

    assert first_client.closed is True
    assert plugin._client is second_client  # type: ignore[comparison-overlap]
    await plugin.on_disable()


def _scene_cache(plugin: MilkyPlugin) -> dict[str, str]:
    return dict(getattr(plugin, "_scene_by_peer"))
