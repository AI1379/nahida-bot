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

    def test_group_message(self) -> None:
        conv = TelegramMessageConverter()
        msg = _make_message(text="hi", chat_type="supergroup")
        result = conv.to_inbound(msg)
        assert result.is_group is True

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
