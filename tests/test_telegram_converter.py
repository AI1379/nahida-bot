"""Tests for TelegramMessageConverter."""

from nahida_bot.channels.telegram.message_converter import TelegramMessageConverter


def _make_message(
    *,
    text: str = "hello",
    chat_id: int = 123,
    chat_type: str = "private",
    user_id: int = 456,
    message_id: int = 789,
    reply_to: dict | None = None,
    date: int = 1700000000,
) -> dict:
    """Build a minimal Telegram message dict."""
    msg: dict = {
        "message_id": message_id,
        "date": date,
        "chat": {"id": chat_id, "type": chat_type},
        "from_user": {"id": user_id, "first_name": "Test"},
        "text": text,
    }
    if reply_to is not None:
        msg["reply_to_message"] = reply_to
    return msg


class TestTelegramMessageConverter:
    def test_simple_text_message(self) -> None:
        conv = TelegramMessageConverter()
        msg = _make_message(text="hello world")
        result = conv.to_inbound(msg)

        assert result.platform == "telegram"
        assert result.text == "hello world"
        assert result.chat_id == "123"
        assert result.user_id == "456"
        assert result.message_id == "789"
        assert result.is_group is False
        assert result.command_prefix == "/"
        assert result.message_context is not None
        assert result.message_context.channel == "telegram"
        assert result.message_context.chat_type == "private"
        assert result.message_context.sender_display_name == "Test"

    def test_group_message(self) -> None:
        conv = TelegramMessageConverter()
        msg = _make_message(text="hi", chat_type="supergroup")
        result = conv.to_inbound(msg)
        assert result.is_group is True
        assert result.message_context is not None
        assert result.message_context.chat_type == "group"

    def test_group_message_strips_mention(self) -> None:
        conv = TelegramMessageConverter(bot_username="mybot")
        msg = _make_message(text="@mybot /help", chat_type="supergroup")
        result = conv.to_inbound(msg)
        assert result.text == "/help"

    def test_group_message_strips_mention_with_text(self) -> None:
        conv = TelegramMessageConverter(bot_username="mybot")
        msg = _make_message(text="@mybot what is 2+2?", chat_type="supergroup")
        result = conv.to_inbound(msg)
        assert result.text == "what is 2+2?"

    def test_private_message_no_strip(self) -> None:
        conv = TelegramMessageConverter(bot_username="mybot")
        msg = _make_message(text="@mybot hello", chat_type="private")
        result = conv.to_inbound(msg)
        assert result.text == "@mybot hello"

    def test_group_message_no_mention_no_strip(self) -> None:
        conv = TelegramMessageConverter(bot_username="mybot")
        msg = _make_message(text="just chatting", chat_type="supergroup")
        result = conv.to_inbound(msg)
        assert result.text == "just chatting"

    def test_command_preserved(self) -> None:
        conv = TelegramMessageConverter()
        msg = _make_message(text="/start")
        result = conv.to_inbound(msg)
        assert result.text == "/start"
        assert result.command_prefix == "/"

    def test_reply_to_populated(self) -> None:
        conv = TelegramMessageConverter()
        msg = _make_message(
            text="reply",
            reply_to={"message_id": 100},
        )
        result = conv.to_inbound(msg)
        assert result.reply_to == "100"

    def test_no_reply_to(self) -> None:
        conv = TelegramMessageConverter()
        msg = _make_message(text="no reply")
        result = conv.to_inbound(msg)
        assert result.reply_to == ""

    def test_timestamp_from_date(self) -> None:
        conv = TelegramMessageConverter()
        msg = _make_message(text="hi", date=1700000000)
        result = conv.to_inbound(msg)
        assert result.timestamp == 1700000000.0

    def test_empty_text_returns_empty(self) -> None:
        conv = TelegramMessageConverter()
        msg = _make_message(text="")
        result = conv.to_inbound(msg)
        assert result.text == ""

    def test_no_bot_username_no_mention_strip(self) -> None:
        conv = TelegramMessageConverter(bot_username=None)
        msg = _make_message(text="@mybot /help", chat_type="supergroup")
        result = conv.to_inbound(msg)
        assert result.text == "@mybot /help"


class TestTelegramAttachmentExtraction:
    """Tests for _extract_attachments covering Telegram media types."""

    def test_no_attachments(self) -> None:
        msg = _make_message(text="just text")
        result = TelegramMessageConverter._extract_attachments(msg)
        assert result == []

    def test_photo_picks_largest_size(self) -> None:
        msg = _make_message(text="")
        msg["photo"] = [
            {"file_id": "small", "width": 90, "height": 90},
            {"file_id": "medium", "width": 320, "height": 320},
            {"file_id": "large", "width": 800, "height": 800},
        ]
        result = TelegramMessageConverter._extract_attachments(msg)
        assert len(result) == 1
        assert result[0].kind == "image"
        assert result[0].platform_id == "large"
        assert result[0].width == 800
        assert result[0].height == 800

    def test_malformed_numeric_media_fields_do_not_crash(self) -> None:
        msg = _make_message(text="")
        msg["photo"] = [{"file_id": "img", "width": "wide", "height": None}]
        msg["document"] = {
            "file_id": "doc",
            "mime_type": "application/pdf",
            "file_size": "large",
        }

        result = TelegramMessageConverter._extract_attachments(msg)

        assert result[0].width == 0
        assert result[0].height == 0
        assert result[1].file_size == 0

    def test_sticker_creates_image_attachment(self) -> None:
        msg = _make_message(text="")
        msg["sticker"] = {"file_id": "sticker_123", "emoji": "😊"}
        result = TelegramMessageConverter._extract_attachments(msg)
        assert len(result) == 1
        assert result[0].kind == "image"
        assert result[0].platform_id == "sticker_123"
        assert result[0].alt_text == "😊"
        assert result[0].metadata == {"sticker": True}

    def test_sticker_skipped_when_photo_present(self) -> None:
        msg = _make_message(text="")
        msg["photo"] = [{"file_id": "img", "width": 100, "height": 100}]
        msg["sticker"] = {"file_id": "sticker_123", "emoji": "😊"}
        result = TelegramMessageConverter._extract_attachments(msg)
        assert len(result) == 1
        assert result[0].platform_id == "img"

    def test_document_attachment(self) -> None:
        msg = _make_message(text="here is a file")
        msg["document"] = {
            "file_id": "doc_abc",
            "mime_type": "application/pdf",
            "file_size": 2048,
            "file_name": "report.pdf",
        }
        result = TelegramMessageConverter._extract_attachments(msg)
        assert len(result) == 1
        assert result[0].kind == "file"
        assert result[0].platform_id == "doc_abc"
        assert result[0].mime_type == "application/pdf"
        assert result[0].file_size == 2048
        assert result[0].metadata == {"file_name": "report.pdf"}

    def test_video_attachment(self) -> None:
        msg = _make_message(text="")
        msg["video"] = {
            "file_id": "vid_xyz",
            "mime_type": "video/mp4",
            "file_size": 102400,
            "width": 1280,
            "height": 720,
        }
        result = TelegramMessageConverter._extract_attachments(msg)
        assert len(result) == 1
        assert result[0].kind == "video"
        assert result[0].platform_id == "vid_xyz"
        assert result[0].width == 1280
        assert result[0].height == 720

    def test_audio_attachment(self) -> None:
        msg = _make_message(text="")
        msg["audio"] = {
            "file_id": "aud_001",
            "mime_type": "audio/mpeg",
            "file_size": 5120,
            "file_name": "song.mp3",
        }
        result = TelegramMessageConverter._extract_attachments(msg)
        assert len(result) == 1
        assert result[0].kind == "audio"
        assert result[0].mime_type == "audio/mpeg"

    def test_voice_attachment(self) -> None:
        msg = _make_message(text="")
        msg["voice"] = {
            "file_id": "voice_99",
            "mime_type": "audio/ogg",
            "duration": 15,
        }
        result = TelegramMessageConverter._extract_attachments(msg)
        assert len(result) == 1
        assert result[0].kind == "audio"
        assert result[0].metadata == {"duration": 15, "voice": True}

    def test_animation_attachment(self) -> None:
        msg = _make_message(text="")
        msg["animation"] = {
            "file_id": "gif_42",
            "mime_type": "video/mp4",
        }
        result = TelegramMessageConverter._extract_attachments(msg)
        assert len(result) == 1
        assert result[0].kind == "video"
        assert result[0].metadata == {"animation": True}

    def test_multiple_attachments_combined(self) -> None:
        msg = _make_message(text="check this out")
        msg["photo"] = [{"file_id": "img_1", "width": 640, "height": 480}]
        msg["document"] = {
            "file_id": "doc_1",
            "mime_type": "text/plain",
            "file_size": 100,
        }
        result = TelegramMessageConverter._extract_attachments(msg)
        assert len(result) == 2
        assert result[0].kind == "image"
        assert result[1].kind == "file"

    def test_to_inbound_populates_attachments(self) -> None:
        conv = TelegramMessageConverter()
        msg = _make_message(text="nice pic")
        msg["photo"] = [{"file_id": "img_big", "width": 1024, "height": 768}]
        result = conv.to_inbound(msg)
        assert len(result.attachments) == 1
        assert result.attachments[0].kind == "image"
        assert result.attachments[0].platform_id == "img_big"

    def test_to_inbound_empty_attachments_when_no_media(self) -> None:
        conv = TelegramMessageConverter()
        msg = _make_message(text="text only")
        result = conv.to_inbound(msg)
        assert result.attachments == []
