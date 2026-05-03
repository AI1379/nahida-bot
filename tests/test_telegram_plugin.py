"""Tests for TelegramChannelPlugin."""

from __future__ import annotations

import os
import tempfile
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nahida_bot.channels.telegram.plugin import TelegramChannelPlugin
from nahida_bot.plugins.manifest import PluginManifest


def _make_manifest(**overrides: object) -> PluginManifest:
    defaults = {
        "id": "telegram",
        "name": "Telegram Channel",
        "version": "0.1.0",
        "entrypoint": "nahida_bot.channels.telegram.plugin:TelegramChannelPlugin",
        "type": "channel",
        "config": {"bot_token": "test-token-123"},
    }
    defaults.update(overrides)  # type: ignore[typeddict-item]
    return PluginManifest(**defaults)  # type: ignore[arg-type]


class _MockAPI:
    """Minimal BotAPI mock satisfying the BotAPI protocol for testing."""

    def __init__(self) -> None:
        self.published_events: list[Any] = []
        self.registered_tools: dict[str, dict[str, Any]] = {}
        self.registered_channels: list[Any] = []

    async def send_message(
        self, target: str, message: Any, *, channel: str = ""
    ) -> str:
        return ""

    def on_event(self, event_type: type) -> Any:
        return lambda f: f

    def subscribe(self, event_type: type, handler: Any) -> Any:
        return None

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Any,
    ) -> None:
        self.registered_tools[name] = {
            "description": description,
            "parameters": parameters,
            "handler": handler,
        }

    def register_channel(self, channel: Any) -> None:
        self.registered_channels.append(channel)

    def register_provider_type(
        self,
        type_key: str,
        factory: Any,
        *,
        config_schema: dict[str, Any] | None = None,
        description: str = "",
    ) -> None:
        pass

    def register_command(
        self,
        name: str,
        handler: Any,
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

    @property
    def scheduler_service(self) -> Any | None:
        return None

    async def memory_store(
        self, key: str, content: str, *, metadata: dict[str, Any] | None = None
    ) -> None:
        pass

    async def workspace_read(self, path: str) -> str:
        return ""

    async def workspace_write(self, path: str, content: str) -> None:
        pass

    async def publish_event(self, event: Any) -> None:
        self.published_events.append(event)

    @property
    def logger(self) -> Any:
        from unittest.mock import MagicMock

        return MagicMock()


class TestTelegramChannelPluginLifecycle:
    async def test_on_load_creates_bot(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest()
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)

        mock_bot = AsyncMock()
        mock_me = MagicMock()
        mock_me.username = "testbot"
        mock_me.id = 12345
        mock_bot.get_me.return_value = mock_me

        with patch("aiogram.Bot", return_value=mock_bot):
            await plugin.on_load()

        assert plugin._bot is mock_bot

    async def test_on_load_raises_without_token(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest(config={"bot_token": ""})
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            with pytest.raises(RuntimeError, match="bot_token not configured"):
                await plugin.on_load()

    async def test_on_enable_starts_polling(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest()
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)
        plugin._bot = AsyncMock()  # Pretend on_load succeeded

        await plugin.on_enable()
        assert plugin._polling_task is not None

        # Cleanup
        await plugin.on_disable()

    async def test_on_disable_stops_polling(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest()
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)
        plugin._bot = AsyncMock()

        await plugin.on_enable()
        assert plugin._polling_task is not None

        await plugin.on_disable()
        assert plugin._polling_task is None


class TestTelegramChannelPluginMessaging:
    async def test_send_message_converts_markdown_to_html(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest()
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)

        mock_bot = AsyncMock()
        mock_sent = MagicMock()
        mock_sent.message_id = 42
        mock_bot.send_message.return_value = mock_sent
        plugin._bot = mock_bot

        from nahida_bot.plugins.base import OutboundMessage

        await plugin.send_message("123", OutboundMessage(text="**bold** and `code`"))

        call_kwargs = mock_bot.send_message.call_args[1]
        assert "<b>bold</b>" in call_kwargs["text"]
        assert "<code>code</code>" in call_kwargs["text"]
        assert "**bold**" not in call_kwargs["text"]

    async def test_send_message_plain_text(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest()
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)

        mock_bot = AsyncMock()
        mock_sent = MagicMock()
        mock_sent.message_id = 42
        mock_bot.send_message.return_value = mock_sent
        plugin._bot = mock_bot

        from nahida_bot.plugins.base import OutboundMessage

        await plugin.send_message("123", OutboundMessage(text="just plain text"))

        mock_bot.send_message.assert_awaited_once()
        call_kwargs = mock_bot.send_message.call_args[1]
        assert call_kwargs["chat_id"] == 123
        assert call_kwargs["text"] == "just plain text"
        assert call_kwargs.get("reply_to_message_id") is None

    async def test_send_message_with_reply(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest()
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)

        mock_bot = AsyncMock()
        mock_sent = MagicMock()
        mock_sent.message_id = 43
        mock_bot.send_message.return_value = mock_sent
        plugin._bot = mock_bot

        from nahida_bot.plugins.base import OutboundMessage

        await plugin.send_message("123", OutboundMessage(text="reply", reply_to="10"))

        call_kwargs = mock_bot.send_message.call_args[1]
        assert call_kwargs["reply_to_message_id"] == 10

    async def test_send_message_retries_retry_after(self) -> None:
        class _RetryAfter(Exception):
            retry_after = 0

        api = _MockAPI()
        manifest = _make_manifest(
            config={"bot_token": "test", "send_retry_attempts": 2}
        )
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)

        mock_bot = AsyncMock()
        mock_sent = MagicMock()
        mock_sent.message_id = 44
        mock_bot.send_message.side_effect = [_RetryAfter(), mock_sent]
        plugin._bot = mock_bot

        from nahida_bot.plugins.base import OutboundMessage

        with patch("asyncio.sleep", new=AsyncMock()) as sleep_mock:
            result = await plugin.send_message("123", OutboundMessage(text="hi"))

        assert result == "44"
        assert mock_bot.send_message.await_count == 2
        sleep_mock.assert_awaited_once_with(0.0)


class TestTelegramInboundMedia:
    async def test_handle_inbound_publishes_event(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest()
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)

        update = {
            "message": {
                "message_id": 1,
                "date": 1700000000,
                "chat": {"id": 100, "type": "private"},
                "from_user": {"id": 200, "first_name": "User"},
                "text": "/help",
            }
        }

        await plugin.handle_inbound_event(update)

        assert len(api.published_events) == 1
        event = api.published_events[0]
        inbound = event.payload.message
        assert inbound.text == "/help"
        assert inbound.platform == "telegram"
        assert inbound.chat_id == "100"

    async def test_handle_inbound_photo_with_caption(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest()
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)

        update = {
            "message": {
                "message_id": 2,
                "date": 1700000000,
                "chat": {"id": 100, "type": "private"},
                "from_user": {"id": 200},
                "photo": [
                    {"file_id": "small", "width": 160, "height": 160},
                    {"file_id": "big_photo", "width": 800, "height": 600},
                ],
                "caption": "look at this",
            }
        }

        await plugin.handle_inbound_event(update)

        assert len(api.published_events) == 1
        inbound = api.published_events[0].payload.message
        assert "look at this" in inbound.text
        assert "[Media: type=photo, file_id=big_photo]" in inbound.text

    async def test_handle_inbound_photo_no_caption(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest()
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)

        update = {
            "message": {
                "message_id": 3,
                "date": 1700000000,
                "chat": {"id": 100, "type": "private"},
                "from_user": {"id": 200},
                "photo": [{"file_id": "photo_abc", "width": 640, "height": 480}],
            }
        }

        await plugin.handle_inbound_event(update)

        assert len(api.published_events) == 1
        inbound = api.published_events[0].payload.message
        assert "[Media: type=photo, file_id=photo_abc]" in inbound.text

    async def test_handle_inbound_document(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest()
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)

        update = {
            "message": {
                "message_id": 4,
                "date": 1700000000,
                "chat": {"id": 100, "type": "private"},
                "from_user": {"id": 200},
                "document": {
                    "file_id": "doc_xyz",
                    "file_name": "report.pdf",
                    "mime_type": "application/pdf",
                    "file_size": 1024,
                },
            }
        }

        await plugin.handle_inbound_event(update)

        assert len(api.published_events) == 1
        inbound = api.published_events[0].payload.message
        assert "[Media: type=document, file_id=doc_xyz]" in inbound.text

    async def test_handle_inbound_sticker(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest()
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)

        update = {
            "message": {
                "message_id": 5,
                "date": 1700000000,
                "chat": {"id": 100, "type": "private"},
                "from_user": {"id": 200},
                "sticker": {"file_id": "sticker_abc", "emoji": "😀"},
            }
        }

        await plugin.handle_inbound_event(update)
        assert len(api.published_events) == 1
        inbound = api.published_events[0].payload.message
        assert "[Media: type=sticker, file_id=sticker_abc]" in inbound.text

    async def test_handle_inbound_ignores_empty_message(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest()
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)

        # No message field at all (e.g. callback_query)
        update = {"callback_query": {"id": "1", "data": "click"}}

        await plugin.handle_inbound_event(update)
        assert len(api.published_events) == 0

    def test_extract_media_info_photo(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest()
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)

        msg_data = {
            "photo": [
                {"file_id": "small", "width": 160, "height": 160},
                {"file_id": "large", "width": 800, "height": 600},
            ]
        }
        info = plugin._extract_media_info(msg_data)
        assert info is not None
        assert info["type"] == "photo"
        assert info["file_id"] == "large"
        assert info["width"] == 800

    def test_extract_media_info_document(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest()
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)

        msg_data = {
            "document": {
                "file_id": "doc123",
                "file_name": "test.txt",
                "mime_type": "text/plain",
                "file_size": 42,
            }
        }
        info = plugin._extract_media_info(msg_data)
        assert info is not None
        assert info["type"] == "document"
        assert info["file_id"] == "doc123"
        assert info["file_name"] == "test.txt"

    def test_extract_media_info_returns_none_for_text(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest()
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)

        info = plugin._extract_media_info({"text": "hello"})
        assert info is None


class TestTelegramOutboundAttachments:
    async def test_send_photo_attachment(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest()
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)

        mock_bot = AsyncMock()
        mock_sent = MagicMock()
        mock_sent.message_id = 55
        mock_bot.send_photo.return_value = mock_sent
        plugin._bot = mock_bot

        from nahida_bot.plugins.base import Attachment, OutboundMessage

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x89PNG\r\n\x1a\n")
            tmp_path = f.name

        try:
            msg = OutboundMessage(
                text="",
                attachments=[
                    Attachment(type="photo", path=tmp_path, filename="test.png")
                ],
            )
            result = await plugin.send_message("123", msg)

            mock_bot.send_photo.assert_awaited_once()
            call_kwargs = mock_bot.send_photo.call_args[1]
            assert call_kwargs["chat_id"] == 123
            assert result == "55"
        finally:
            os.unlink(tmp_path)

    async def test_send_document_attachment(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest()
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)

        mock_bot = AsyncMock()
        mock_sent = MagicMock()
        mock_sent.message_id = 56
        mock_bot.send_document.return_value = mock_sent
        plugin._bot = mock_bot

        from nahida_bot.plugins.base import Attachment, OutboundMessage

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4")
            tmp_path = f.name

        try:
            msg = OutboundMessage(
                text="",
                attachments=[
                    Attachment(type="document", path=tmp_path, filename="report.pdf")
                ],
            )
            result = await plugin.send_message("123", msg)

            mock_bot.send_document.assert_awaited_once()
            call_kwargs = mock_bot.send_document.call_args[1]
            assert call_kwargs["chat_id"] == 123
            assert result == "56"
        finally:
            os.unlink(tmp_path)

    async def test_send_attachment_missing_file(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest()
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)

        mock_bot = AsyncMock()
        plugin._bot = mock_bot

        from nahida_bot.plugins.base import Attachment, OutboundMessage

        msg = OutboundMessage(
            text="",
            attachments=[Attachment(type="photo", path="/nonexistent/file.png")],
        )
        result = await plugin.send_message("123", msg)

        mock_bot.send_photo.assert_not_awaited()
        assert result == ""


class TestTelegramDownloadMedia:
    async def test_on_enable_registers_download_tool(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest()
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)
        plugin._bot = AsyncMock()

        await plugin.on_enable()
        await plugin.on_disable()

        assert "download_media" in api.registered_tools
        tool = api.registered_tools["download_media"]
        assert "file_id" in tool["parameters"]["properties"]
        assert tool["parameters"]["required"] == ["file_id"]

    async def test_download_media_returns_result(self) -> None:
        api = _MockAPI()
        tmp_dir = tempfile.mkdtemp()
        manifest = _make_manifest(
            config={
                "bot_token": "test",
                "media_download_dir": tmp_dir,
            }
        )
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)

        mock_bot = AsyncMock()
        mock_file = MagicMock()
        mock_file.file_path = "photos/file_123.jpg"
        mock_bot.get_file.return_value = mock_file

        async def _fake_download(fp: str, destination: str = "") -> None:
            with open(destination, "wb") as f:
                f.write(b"fake data")

        mock_bot.download_file.side_effect = _fake_download
        plugin._bot = mock_bot

        result = await plugin.download_media("test_file_id")

        mock_bot.get_file.assert_awaited_once_with("test_file_id")
        mock_bot.download_file.assert_awaited_once()
        assert result is not None
        assert "test_file_id" in result.path
        assert result.file_name == "test_file_id.dat"
        assert result.file_size == 9

    async def test_download_media_with_destination(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest(config={"bot_token": "test"})
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)

        mock_bot = AsyncMock()
        mock_file = MagicMock()
        mock_file.file_path = "photos/file.jpg"
        mock_bot.get_file.return_value = mock_file

        async def _fake_download(fp: str, destination: str = "") -> None:
            with open(destination, "wb") as f:
                f.write(b"fake data")

        mock_bot.download_file.side_effect = _fake_download
        plugin._bot = mock_bot

        with tempfile.TemporaryDirectory() as tmp_dir:
            dest = os.path.join(tmp_dir, "custom_name.jpg")
            result = await plugin.download_media("fid123", destination=dest)

            assert result is not None
            assert result.path == dest

    async def test_download_media_returns_none_without_bot(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest()
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)
        plugin._bot = None

        result = await plugin.download_media("any_id")
        assert result is None

    async def test_download_media_returns_none_on_get_file_failure(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest()
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)

        mock_bot = AsyncMock()
        mock_bot.get_file.side_effect = Exception("file not found")
        plugin._bot = mock_bot

        result = await plugin.download_media("bad_id")
        assert result is None

    async def test_download_tool_handler_success(self) -> None:
        api = _MockAPI()
        media_dir = tempfile.mkdtemp()
        manifest = _make_manifest(
            config={
                "bot_token": "test",
                "media_download_dir": media_dir,
            }
        )
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)
        plugin._bot = AsyncMock()

        await plugin.on_enable()

        tool = api.registered_tools["download_media"]
        handler = tool["handler"]

        # Create a small file to simulate download
        mock_bot = plugin._bot
        mock_file = MagicMock()
        mock_file.file_path = "photos/test.jpg"

        async def _fake_download(fp: str, destination: str = "") -> None:
            with open(destination, "wb") as f:
                f.write(b"fake image data")

        mock_bot.get_file.return_value = mock_file
        mock_bot.download_file.side_effect = _fake_download

        result_str = await handler(file_id="test_fid")
        import json

        result = json.loads(result_str)
        assert "path" in result
        assert result["file_size"] > 0

        await plugin.on_disable()

    async def test_base_channel_plugin_download_returns_none(self) -> None:
        """ChannelPlugin base class returns None by default."""
        from nahida_bot.plugins.channel_plugin import ChannelPlugin

        class _ConcreteChannel(ChannelPlugin):
            async def on_load(self) -> None:
                pass

            async def handle_inbound_event(self, event: dict[str, Any]) -> None:
                pass

            async def send_message(self, target: str, message: Any) -> str:
                return ""

        api = _MockAPI()
        manifest = _make_manifest()
        plugin = _ConcreteChannel(api=api, manifest=manifest)
        result = await plugin.download_media("any_file_id")
        assert result is None


class TestTelegramChannelPluginCommunicationFlags:
    def test_supports_http_client(self) -> None:
        assert TelegramChannelPlugin.SUPPORT_HTTP_CLIENT is True
        assert TelegramChannelPlugin.SUPPORT_HTTP_SERVER is False
        assert TelegramChannelPlugin.SUPPORT_WEBSOCKET_CLIENT is False
        assert TelegramChannelPlugin.SUPPORT_WEBSOCKET_SERVER is False
        assert TelegramChannelPlugin.SUPPORT_SSE is False
