"""Telegram Message ↔ InboundMessage conversion."""

from __future__ import annotations

import time
from typing import Any

from nahida_bot.plugins.base import InboundMessage


class TelegramMessageConverter:
    """Converts aiogram Message objects to InboundMessage.

    Handles @mention stripping in group chats and preserves command prefixes.
    """

    def __init__(self, bot_username: str | None = None) -> None:
        self._bot_username = bot_username

    def to_inbound(self, msg_data: dict[str, Any]) -> InboundMessage:
        """Convert a Telegram message dict to InboundMessage.

        Args:
            msg_data: A Telegram Message object serialised as a dict
                (from ``update.model_dump()`` or similar).
        """
        text = msg_data.get("text") or ""
        chat = msg_data.get("chat", {})
        from_user = msg_data.get("from_user") or msg_data.get("from", {})
        chat_type = chat.get("type", "private")

        is_group = chat_type in ("group", "supergroup")

        # Strip @mention in groups: "@botname /help" → "/help"
        if is_group and self._bot_username:
            mention = f"@{self._bot_username}"
            if text.strip().startswith(mention):
                text = text.strip()[len(mention) :].strip()

        reply_to_data = msg_data.get("reply_to_message")
        reply_to = ""
        if reply_to_data and isinstance(reply_to_data, dict):
            reply_to = str(reply_to_data.get("message_id", ""))

        msg_date = msg_data.get("date")
        timestamp = msg_date if isinstance(msg_date, (int, float)) else time.time()

        return InboundMessage(
            message_id=str(msg_data.get("message_id", "0")),
            platform="telegram",
            chat_id=str(chat.get("id", "")),
            user_id=str(from_user.get("id", "0")),
            text=text,
            raw_event=msg_data,
            is_group=is_group,
            reply_to=reply_to,
            timestamp=float(timestamp),
            command_prefix="/",
        )
