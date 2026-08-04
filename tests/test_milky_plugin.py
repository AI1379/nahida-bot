"""Tests for Milky channel plugin lifecycle and routing."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from nahida_bot.channels.milky.client import MilkyAPIError, MilkyNetworkError
from nahida_bot.channels.milky.plugin import MilkyPlugin
from nahida_bot.channels.milky.segments import OutgoingTextSegment, OutgoingVideoSegment
from nahida_bot.core.events import (
    MessageObserved,
    MessageReactionEvent,
    MessageReceived,
    PokeEvent,
)
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

    async def get_private_file_download_url(
        self, user_id: int, file_id: str, file_hash: str = ""
    ) -> str:
        return f"https://example.com/private/{file_id}"

    async def get_group_file_download_url(self, group_id: int, file_id: str) -> str:
        return f"https://example.com/group/{file_id}"

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


async def test_friend_file_upload_registers_pending_without_publishing() -> None:
    """A friend file upload queues the file and never triggers the agent."""
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    with _patch_downloads(plugin):
        await plugin.handle_inbound_event(_friend_file_upload_event())

    assert api.published_events == []
    pending = _pending_files(plugin)
    assert len(pending) == 1
    entry = pending[0]
    assert entry["scene"] == "friend"
    assert entry["peer_id"] == "10001"
    assert entry["file_id"] == "file-1"
    assert entry["file_name"] == "report.pdf"
    assert entry["file_hash"] == "abc"


async def test_friend_file_upload_downloads_immediately() -> None:
    """The pending entry carries a local path when the download succeeds."""
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    client = _FakeClient()
    plugin._client = client  # type: ignore[assignment]
    await plugin.on_load()

    from nahida_bot.plugins.base import MediaDownloadResult

    downloaded = MediaDownloadResult(
        path="C:/cache/report.pdf",
        file_name="report.pdf",
        mime_type="application/pdf",
        file_size=1024,
    )
    with patch.object(
        plugin,
        "_stream_download_url",
        new=AsyncMock(return_value=downloaded),
    ):
        await plugin.handle_inbound_event(_friend_file_upload_event())

    assert api.published_events == []
    entry = _pending_files(plugin)[0]
    assert entry["path"] == "C:/cache/report.pdf"
    assert entry["mime_type"] == "application/pdf"


async def test_file_only_message_receive_registers_pending_without_publishing() -> None:
    """A message_receive whose only content is a file queues, never triggers."""
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    with _patch_downloads(plugin):
        await plugin.handle_inbound_event(_friend_file_message_event())

    assert api.published_events == []
    pending = _pending_files(plugin)
    assert len(pending) == 1
    assert pending[0]["file_id"] == "file-1"
    # The message_receive file segment carries no file_hash; the upload
    # event is what completes the download chain.
    assert pending[0]["file_hash"] == ""


async def test_text_message_consumes_pending_and_injects_file() -> None:
    """The next text message triggers once and carries the pending file."""
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    client = _FakeClient()
    plugin._client = client  # type: ignore[assignment]
    await plugin.on_load()

    from nahida_bot.plugins.base import MediaDownloadResult

    downloaded = MediaDownloadResult(
        path="C:/cache/report.pdf",
        file_name="report.pdf",
        mime_type="application/pdf",
        file_size=1024,
    )
    with patch.object(
        plugin,
        "_stream_download_url",
        new=AsyncMock(return_value=downloaded),
    ):
        await plugin.handle_inbound_event(_friend_file_upload_event())
        await plugin.handle_inbound_event(_text_message_event())

    assert len(api.published_events) == 1
    event = api.published_events[0]
    assert isinstance(event, MessageReceived)
    inbound = event.payload.message
    assert "report.pdf" in inbound.text
    assert "[File: name=report.pdf, file_id=file-1, size=1024]" in inbound.text
    assert _pending_files(plugin) == []
    file_attachments = [att for att in inbound.attachments if att.kind == "file"]
    assert len(file_attachments) == 1
    assert file_attachments[0].platform_id == "file-1"
    assert file_attachments[0].path == "C:/cache/report.pdf"


async def test_text_message_without_pending_triggers_normally() -> None:
    """Plain text messages keep their normal single-trigger behavior."""
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    await plugin.handle_inbound_event(_text_message_event())

    assert len(api.published_events) == 1
    assert isinstance(api.published_events[0], MessageReceived)


async def test_file_only_and_file_upload_same_file_register_once() -> None:
    """Both events for one file merge into a single pending entry."""
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    with _patch_downloads(plugin):
        await plugin.handle_inbound_event(_friend_file_message_event())
        await plugin.handle_inbound_event(_friend_file_upload_event())

    assert api.published_events == []
    pending = _pending_files(plugin)
    assert len(pending) == 1
    assert pending[0]["file_hash"] == "abc"


async def test_file_context_cache_keeps_upload_hash_in_both_orders() -> None:
    """download_media() context must keep the upload event's hash.

    The message_receive file segment has no file_hash; when it is processed
    after the upload event it must not overwrite the cached hash, or private
    file downloads break.
    """
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    with _patch_downloads(plugin):
        await plugin.handle_inbound_event(_friend_file_upload_event())
        await plugin.handle_inbound_event(_friend_file_message_event())
    ctx = plugin._file_context_cache["file-1"]
    assert ctx["scene"] == "friend"
    assert ctx["peer_id"] == "10001"
    assert ctx["file_hash"] == "abc"

    # And in the reverse (LLOneBot-style) order.
    api2 = RecordingMockBotAPI()
    plugin2 = MilkyPlugin(api=api2, manifest=_manifest())
    plugin2._client = _FakeClient()  # type: ignore[assignment]
    await plugin2.on_load()
    with _patch_downloads(plugin2):
        await plugin2.handle_inbound_event(_friend_file_message_event())
        await plugin2.handle_inbound_event(_friend_file_upload_event())
    assert plugin2._file_context_cache["file-1"]["file_hash"] == "abc"


async def test_download_failure_completed_by_upload_event() -> None:
    """A hash-less message_receive leaves metadata only; upload event downloads."""
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    from nahida_bot.plugins.base import MediaDownloadResult

    downloaded = MediaDownloadResult(
        path="C:/cache/report.pdf",
        file_name="report.pdf",
        mime_type="application/pdf",
        file_size=1024,
    )
    with _patch_downloads(plugin):
        await plugin.handle_inbound_event(_friend_file_message_event())
    assert _pending_files(plugin)[0]["path"] == ""

    with patch.object(
        plugin,
        "_stream_download_url",
        new=AsyncMock(return_value=downloaded),
    ):
        await plugin.handle_inbound_event(_friend_file_upload_event())
    assert _pending_files(plugin)[0]["path"] == "C:/cache/report.pdf"


async def test_upload_then_message_receive_does_not_redownload() -> None:
    """The merged pending entry skips a second download for the same file."""
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    from nahida_bot.plugins.base import MediaDownloadResult

    downloaded = MediaDownloadResult(
        path="C:/cache/report.pdf",
        file_name="report.pdf",
        mime_type="application/pdf",
        file_size=1024,
    )
    stream = AsyncMock(return_value=downloaded)
    with patch.object(plugin, "_stream_download_url", new=stream):
        await plugin.handle_inbound_event(_friend_file_upload_event())
        await plugin.handle_inbound_event(_friend_file_message_event())

    assert stream.await_count == 1
    assert _pending_files(plugin)[0]["path"] == "C:/cache/report.pdf"


async def test_two_pending_files_injected_into_one_message() -> None:
    """Multiple pending files all attach to the next triggering message."""
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    with _patch_downloads(plugin):
        await plugin.handle_inbound_event(_friend_file_upload_event())
        second = _friend_file_upload_event()
        second["data"]["file_id"] = "file-2"
        second["data"]["file_name"] = "notes.txt"
        await plugin.handle_inbound_event(second)
        await plugin.handle_inbound_event(_text_message_event())

    assert len(api.published_events) == 1
    inbound = api.published_events[0].payload.message
    assert "report.pdf" in inbound.text
    assert "notes.txt" in inbound.text
    assert len(_pending_files(plugin)) == 0
    file_attachments = [att for att in inbound.attachments if att.kind == "file"]
    assert {att.platform_id for att in file_attachments} == {"file-1", "file-2"}


async def test_pending_files_are_per_chat() -> None:
    """Files from peer A never leak into peer B's messages."""
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    with _patch_downloads(plugin):
        await plugin.handle_inbound_event(_friend_file_upload_event())
        other_chat = _text_message_event()
        other_chat["data"]["peer_id"] = 10002
        await plugin.handle_inbound_event(other_chat)

    assert len(api.published_events) == 1
    inbound = api.published_events[0].payload.message
    assert "report.pdf" not in inbound.text
    assert len(_pending_files(plugin)) == 1


async def test_pending_file_cap_evicts_oldest_per_chat() -> None:
    """More than the per-chat cap of pending files evicts the oldest."""
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    with _patch_downloads(plugin):
        for index in range(1, 18):
            event = _friend_file_upload_event()
            event["data"]["file_id"] = f"file-{index}"
            event["data"]["file_name"] = f"f{index}.bin"
            await plugin.handle_inbound_event(event)

    pending = _pending_files(plugin)
    assert len(pending) == 16
    assert pending[0]["file_id"] == "file-2"
    assert pending[-1]["file_id"] == "file-17"


async def test_pending_pruned_on_registration() -> None:
    """Expired entries are dropped even without a consuming message."""
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest(pending_file_ttl_seconds=1.0))
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    import time as time_module

    with _patch_downloads(plugin):
        await plugin.handle_inbound_event(_friend_file_upload_event())
        _pending_files(plugin)[0]["received_at"] = time_module.monotonic() - 10.0
        second = _friend_file_upload_event()
        second["data"]["file_id"] = "file-2"
        await plugin.handle_inbound_event(second)

    pending = _pending_files(plugin)
    assert len(pending) == 1
    assert pending[0]["file_id"] == "file-2"


async def test_message_without_scene_does_not_consume_pending() -> None:
    """An untyped-scene message must not swallow pending files."""
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    with _patch_downloads(plugin):
        await plugin.handle_inbound_event(_friend_file_upload_event())
        unknown_scene = _text_message_event()
        unknown_scene["data"].pop("message_scene")
        await plugin.handle_inbound_event(unknown_scene)

    assert api.published_events == []
    assert len(_pending_files(plugin)) == 1


async def test_reply_with_file_triggers_without_duplicate_render() -> None:
    """A reply+file message runs the agent once; the pending file merges."""
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    with _patch_downloads(plugin):
        await plugin.handle_inbound_event(_friend_file_upload_event())
        await plugin.handle_inbound_event(
            {
                "event_type": "message_receive",
                "data": {
                    "message_scene": "friend",
                    "peer_id": 10001,
                    "sender_id": 10001,
                    "message_seq": 302,
                    "time": 1700000001,
                    "segments": [
                        {
                            "type": "reply",
                            "data": {"message_seq": 100},
                        },
                        {
                            "type": "file",
                            "data": {
                                "file_id": "file-1",
                                "file_name": "report.pdf",
                                "file_size": 1024,
                            },
                        },
                    ],
                },
            }
        )

    assert len(api.published_events) == 1
    assert isinstance(api.published_events[0], MessageReceived)
    inbound = api.published_events[0].payload.message
    assert inbound.text.count("[File: name=report.pdf") == 1
    file_attachments = [att for att in inbound.attachments if att.kind == "file"]
    assert len(file_attachments) == 1
    assert _pending_files(plugin) == []


async def test_pending_file_expires_after_ttl() -> None:
    """Expired pending files are dropped and not injected."""
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest(pending_file_ttl_seconds=1.0))
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    with _patch_downloads(plugin):
        await plugin.handle_inbound_event(_friend_file_upload_event())
        assert len(_pending_files(plugin)) == 1

        import time as time_module

        _pending_files(plugin)[0]["received_at"] = time_module.monotonic() - 10.0

        await plugin.handle_inbound_event(_text_message_event())

    assert len(api.published_events) == 1
    inbound = api.published_events[0].payload.message
    assert "report.pdf" not in inbound.text
    assert _pending_files(plugin) == []


async def test_file_upload_not_allowed_peer_is_dropped() -> None:
    """Allowlist filtering still applies to file upload events."""
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest(allowed_friends=["99999"]))
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    await plugin.handle_inbound_event(_friend_file_upload_event())

    assert api.published_events == []
    assert _pending_files(plugin) == []


async def test_group_file_upload_registers_pending_without_publishing() -> None:
    """Group file uploads queue like private ones (agent runs never fire)."""
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(
        api=api,
        manifest=_manifest(group_context_capture=True, group_trigger_mode="mention"),
    )
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    with _patch_downloads(plugin):
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

    assert api.published_events == []
    pending = _pending_files(plugin)
    assert len(pending) == 1
    assert pending[0]["scene"] == "group"
    assert pending[0]["peer_id"] == "20001"
    assert pending[0]["file_id"] == "group-file-1"


async def test_group_file_only_message_observes_when_capture_enabled() -> None:
    """Group context capture still records file-only messages as observed."""
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(
        api=api,
        manifest=_manifest(group_context_capture=True, group_trigger_mode="mention"),
    )
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    with _patch_downloads(plugin):
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
                        {
                            "type": "file",
                            "data": {
                                "file_id": "group-file-1",
                                "file_name": "slides.pptx",
                                "file_size": 2048,
                            },
                        },
                    ],
                },
            }
        )

    assert len(api.published_events) == 1
    assert isinstance(api.published_events[0], MessageObserved)
    assert len(_pending_files(plugin)) == 1


async def test_group_file_only_message_registers_pending_without_capture() -> None:
    """File-only group messages queue even when group capture is off."""
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(
        api=api,
        manifest=_manifest(group_context_capture=False, group_trigger_mode="mention"),
    )
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    with _patch_downloads(plugin):
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
                        {
                            "type": "file",
                            "data": {
                                "file_id": "group-file-1",
                                "file_name": "slides.pptx",
                                "file_size": 2048,
                            },
                        },
                    ],
                },
            }
        )

    assert api.published_events == []
    assert len(_pending_files(plugin)) == 1


async def test_group_file_only_message_always_mode_registers_pending() -> None:
    """Always mode never runs the agent for a bare group file."""
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest(group_trigger_mode="always"))
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    with _patch_downloads(plugin):
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
                        {
                            "type": "file",
                            "data": {
                                "file_id": "group-file-1",
                                "file_name": "slides.pptx",
                                "file_size": 2048,
                            },
                        },
                    ],
                },
            }
        )

    assert api.published_events == []
    assert len(_pending_files(plugin)) == 1


async def test_group_observed_message_does_not_consume_pending() -> None:
    """Observed (non-triggering) group messages leave pending files queued."""
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(
        api=api,
        manifest=_manifest(group_context_capture=True, group_trigger_mode="mention"),
    )
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    with _patch_downloads(plugin):
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
        # Unmentioned group text: observed, must not consume.
        await plugin.handle_inbound_event(
            {
                "event_type": "message_receive",
                "data": {
                    "message_scene": "group",
                    "peer_id": 20001,
                    "sender_id": 10001,
                    "message_seq": 125,
                    "time": 1700000001,
                    "segments": [{"type": "text", "data": {"text": "聊别的"}}],
                },
            }
        )
        # Triggering mention message: consumes and injects.
        await plugin.handle_inbound_event(
            {
                "event_type": "message_receive",
                "data": {
                    "message_scene": "group",
                    "peer_id": 20001,
                    "sender_id": 10001,
                    "message_seq": 126,
                    "time": 1700000002,
                    "segments": [
                        {"type": "mention", "data": {"user_id": 999, "name": "bot"}},
                        {"type": "text", "data": {"text": "总结一下"}},
                    ],
                },
            }
        )

    assert len(api.published_events) == 2
    observed, received = api.published_events
    assert isinstance(observed, MessageObserved)
    assert isinstance(received, MessageReceived)
    assert "slides.pptx" in received.payload.message.text
    assert _pending_files(plugin) == []


async def test_group_mention_consumes_pending_file() -> None:
    """A triggering group message injects the pending file."""
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(
        api=api,
        manifest=_manifest(group_context_capture=True, group_trigger_mode="mention"),
    )
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    with _patch_downloads(plugin):
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
        await plugin.handle_inbound_event(
            {
                "event_type": "message_receive",
                "data": {
                    "message_scene": "group",
                    "peer_id": 20001,
                    "sender_id": 10001,
                    "message_seq": 124,
                    "time": 1700000000,
                    "segments": [
                        {"type": "mention", "data": {"user_id": 999, "name": "bot"}},
                        {"type": "text", "data": {"text": "总结一下"}},
                    ],
                },
            }
        )

    assert len(api.published_events) == 1
    assert isinstance(api.published_events[0], MessageReceived)
    inbound = api.published_events[0].payload.message
    assert "slides.pptx" in inbound.text
    assert _pending_files(plugin) == []


def _friend_file_message_event() -> dict[str, Any]:
    """A message_receive carrying a file segment (no file_hash, per protocol)."""
    return {
        "event_type": "message_receive",
        "data": {
            "message_scene": "friend",
            "peer_id": 10001,
            "sender_id": 10001,
            "message_seq": 300,
            "time": 1700000000,
            "segments": [
                {
                    "type": "file",
                    "data": {
                        "file_id": "file-1",
                        "file_name": "report.pdf",
                        "file_size": 1024,
                    },
                },
            ],
        },
    }


def _friend_file_upload_event() -> dict[str, Any]:
    return {
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


def _text_message_event() -> dict[str, Any]:
    """A plain private text message from the same peer."""
    return {
        "event_type": "message_receive",
        "data": {
            "message_scene": "friend",
            "peer_id": 10001,
            "sender_id": 10001,
            "message_seq": 301,
            "time": 1700000001,
            "segments": [{"type": "text", "data": {"text": "帮我看看"}}],
        },
    }


def _pending_files(plugin: MilkyPlugin) -> list[dict[str, object]]:
    """Flatten all pending file entries across chats."""
    pending: list[dict[str, object]] = []
    for entries in getattr(plugin, "_pending_files").values():
        pending.extend(entries)
    return pending


def _patch_downloads(plugin: MilkyPlugin):
    """Neutralize real HTTP downloads for tests that don't assert on them."""
    return patch.object(
        plugin, "_stream_download_url", new=AsyncMock(return_value=None)
    )


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


async def test_group_nudge_targeting_bot_publishes_poke_event() -> None:
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    await plugin.handle_inbound_event(
        {
            "event_type": "group_nudge",
            "self_id": 999,
            "data": {
                "group_id": 20001,
                "sender_id": 10001,
                "receiver_id": 999,
                "display_action": "戳了戳",
                "display_suffix": "",
            },
        }
    )

    assert len(api.published_events) == 1
    event = api.published_events[0]
    assert isinstance(event, PokeEvent)
    assert event.payload.scene == "group"
    assert event.payload.group_id == "20001"
    assert event.payload.user_id == "10001"
    assert event.payload.target_user_id == "999"
    assert event.payload.display_action == "戳了戳"
    assert event.payload.session_id == "milky:group:20001"
    assert event.source == "milky"


async def test_group_nudge_not_targeting_bot_is_dropped() -> None:
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    await plugin.handle_inbound_event(
        {
            "event_type": "group_nudge",
            "self_id": 999,
            "data": {
                "group_id": 20001,
                "sender_id": 10001,
                "receiver_id": 88888,
            },
        }
    )

    assert api.published_events == []


async def test_friend_nudge_self_receive_publishes_poke_event() -> None:
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    await plugin.handle_inbound_event(
        {
            "event_type": "friend_nudge",
            "self_id": 999,
            "data": {
                "user_id": 10001,
                "is_self_send": False,
                "is_self_receive": True,
                "display_action": "拍一拍",
            },
        }
    )

    assert len(api.published_events) == 1
    event = api.published_events[0]
    assert isinstance(event, PokeEvent)
    assert event.payload.scene == "friend"
    assert event.payload.group_id == ""
    assert event.payload.user_id == "10001"
    assert event.payload.target_user_id == "999"
    assert event.payload.session_id == "milky:private:10001"


async def test_friend_nudge_self_send_is_dropped() -> None:
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    await plugin.handle_inbound_event(
        {
            "event_type": "friend_nudge",
            "self_id": 999,
            "data": {
                "user_id": 10001,
                "is_self_send": True,
                "is_self_receive": False,
            },
        }
    )

    assert api.published_events == []


async def test_group_reaction_publishes_reaction_event() -> None:
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    await plugin.handle_inbound_event(
        {
            "event_type": "group_message_reaction",
            "self_id": 999,
            "data": {
                "group_id": 20001,
                "user_id": 10001,
                "message_seq": 555,
                "face_id": "76",
                "reaction_type": "face",
                "is_add": True,
            },
        }
    )

    assert len(api.published_events) == 1
    event = api.published_events[0]
    assert isinstance(event, MessageReactionEvent)
    assert event.payload.group_id == "20001"
    assert event.payload.user_id == "10001"
    assert event.payload.message_seq == "555"
    assert event.payload.face_id == "76"
    assert event.payload.reaction_type == "face"
    assert event.payload.is_add is True
    assert event.payload.session_id == "milky:group:20001"


async def test_group_reaction_filtered_by_allowed_groups() -> None:
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest(allowed_groups=["99999"]))
    plugin._client = _FakeClient()  # type: ignore[assignment]
    await plugin.on_load()

    await plugin.handle_inbound_event(
        {
            "event_type": "group_message_reaction",
            "self_id": 999,
            "data": {
                "group_id": 20001,
                "user_id": 10001,
                "message_seq": 555,
                "face_id": "76",
                "is_add": True,
            },
        }
    )

    assert api.published_events == []


async def test_video_native_send_succeeds() -> None:
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    client = _FakeClient()
    plugin._client = client  # type: ignore[assignment]
    await plugin.on_load()

    result = await plugin.send_message(
        "group:20001",
        OutboundMessage(
            text="",
            attachments=[Attachment(type="video", path="file:///tmp/a.mp4")],
        ),
    )

    assert result == "22"
    assert len(client.group_messages) == 1
    segments = client.group_messages[0][1]
    assert isinstance(segments[0], OutgoingVideoSegment)
    assert client.group_files == []


async def test_video_native_fail_falls_back_to_file_upload_with_text() -> None:
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest())
    client = _FakeClient()
    client.fail_next_group_message = True
    plugin._client = client  # type: ignore[assignment]
    await plugin.on_load()

    result = await plugin.send_message(
        "group:20001",
        OutboundMessage(
            text="see this",
            attachments=[Attachment(type="video", path="file:///tmp/a.mp4")],
        ),
    )

    assert len(client.group_files) == 1
    peer_id, upload = client.group_files[0]
    assert peer_id == 20001
    assert upload.file_uri == "file:///tmp/a.mp4"
    assert upload.file_name == "a.mp4"
    assert len(client.group_messages) == 1
    text_seg = client.group_messages[0][1][0]
    assert getattr(text_seg, "text") == "see this"
    assert result == "22"


async def test_video_only_native_fail_falls_back_to_file_upload() -> None:
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
            attachments=[Attachment(type="video", path="file:///tmp/a.mp4")],
        ),
    )

    assert len(client.group_files) == 1
    assert client.group_files[0][1].file_name == "a.mp4"
    assert client.group_messages == []
    assert result == "group-file"


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


# ---------------------------------------------------------------------------
# Issue #28: image/video/record temp_url resolution on the inbound path.
# Upstream Milky events sometimes omit ``temp_url``; the converter used to pass
# an empty URL straight through, so the image was silently dropped downstream.
# These tests pin the proactive ``get_resource_temp_url`` resolution.
# ---------------------------------------------------------------------------


class _RecordingMediaClient(_FakeClient):
    """FakeClient that records ``get_resource_temp_url`` calls."""

    def __init__(self, *, url_template: str = "https://cdn.example.com/{rid}") -> None:
        super().__init__()
        self.temp_url_calls: list[str] = []
        self._url_template = url_template

    async def get_resource_temp_url(self, resource_id: str) -> str:
        self.temp_url_calls.append(resource_id)
        return self._url_template.format(rid=resource_id)


class _FailingMediaClient(_RecordingMediaClient):
    async def get_resource_temp_url(self, resource_id: str) -> str:
        self.temp_url_calls.append(resource_id)
        raise MilkyAPIError("no url", api_name="get_resource_temp_url", retcode=1)


def _image_message_event(
    *, resource_id: str, temp_url: str, scene: str = "friend"
) -> dict[str, Any]:
    return {
        "event_type": "message_receive",
        "data": {
            "message_scene": scene,
            "peer_id": 10001,
            "sender_id": 10001,
            "message_seq": 200,
            "time": 1700000000,
            "segments": [
                {"type": "text", "data": {"text": "看看这张图"}},
                {
                    "type": "image",
                    "data": {
                        "resource_id": resource_id,
                        "temp_url": temp_url,
                    },
                },
            ],
        },
    }


async def test_image_with_empty_temp_url_resolves_via_api() -> None:
    """Empty temp_url ⇒ plugin proactively calls get_resource_temp_url (#28)."""
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest(cache_media_on_receive=False))
    client = _RecordingMediaClient()
    plugin._client = client  # type: ignore[assignment]
    await plugin.on_load()

    await plugin.handle_inbound_event(
        _image_message_event(resource_id="img-1", temp_url="")
    )

    assert client.temp_url_calls == ["img-1"]
    assert len(api.published_events) == 1
    attachment = api.published_events[0].payload.message.attachments[0]
    assert attachment.kind == "image"
    assert attachment.url == "https://cdn.example.com/img-1"
    assert attachment.metadata.get("trusted_url") is True


async def test_image_with_present_temp_url_skips_api_call() -> None:
    """A pre-filled temp_url must not trigger a redundant API call."""
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest(cache_media_on_receive=False))
    client = _RecordingMediaClient()
    plugin._client = client  # type: ignore[assignment]
    await plugin.on_load()

    await plugin.handle_inbound_event(
        _image_message_event(resource_id="img-2", temp_url="https://pre/a.png")
    )

    assert client.temp_url_calls == []
    attachment = api.published_events[0].payload.message.attachments[0]
    assert attachment.url == "https://pre/a.png"


async def test_image_temp_url_resolution_failure_is_swallowed() -> None:
    """A failing get_resource_temp_url must not crash inbound handling."""
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest(cache_media_on_receive=False))
    client = _FailingMediaClient()
    plugin._client = client  # type: ignore[assignment]
    await plugin.on_load()

    await plugin.handle_inbound_event(
        _image_message_event(resource_id="img-3", temp_url="")
    )

    assert client.temp_url_calls == ["img-3"]
    # Message still published; url stays empty (downstream surfaces a notice).
    attachment = api.published_events[0].payload.message.attachments[0]
    assert attachment.url == ""


async def test_image_eager_download_materializes_local_path(
    tmp_path,
) -> None:
    """cache_media_on_receive ⇒ resolved temp_url is eagerly downloaded (#28)."""
    api = RecordingMockBotAPI()
    plugin = MilkyPlugin(api=api, manifest=_manifest(cache_media_on_receive=True))
    client = _RecordingMediaClient()
    plugin._client = client  # type: ignore[assignment]
    await plugin.on_load()

    from nahida_bot.plugins.base import MediaDownloadResult

    downloaded = MediaDownloadResult(
        path=str(tmp_path / "img.png"),
        file_name="img.png",
        mime_type="image/png",
        file_size=123,
    )
    with patch.object(
        plugin,
        "_stream_download_url",
        new=AsyncMock(return_value=downloaded),
    ):
        await plugin.handle_inbound_event(
            _image_message_event(resource_id="img-4", temp_url="")
        )

    assert client.temp_url_calls == ["img-4"]
    attachment = api.published_events[0].payload.message.attachments[0]
    assert attachment.url == "https://cdn.example.com/img-4"
    assert attachment.path == str(tmp_path / "img.png")
    assert attachment.mime_type == "image/png"
