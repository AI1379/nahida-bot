"""Telegram Message ↔ InboundMessage conversion."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any

from nahida_bot.core.message_context import (
    chat_context_from_values,
    context_from_inbound,
    sender_context_from_values,
)
from nahida_bot.plugins.base import InboundAttachment, InboundMessage


class TelegramMessageConverter:
    """Converts aiogram Message objects to InboundMessage.

    Handles @mention stripping in group chats and preserves command prefixes.
    """

    def __init__(
        self,
        bot_username: str | None = None,
        bot_user_id: str | int | None = None,
    ) -> None:
        self._bot_username = bot_username
        self._bot_user_id = str(bot_user_id) if bot_user_id is not None else ""

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
        mentions_bot = False
        mentioned_user_ids: tuple[str, ...] = ()

        # Strip @mention in groups: "@botname /help" → "/help"
        if is_group and self._bot_username:
            mention = f"@{self._bot_username}"
            mentions_bot = mention.lower() in text.lower()
            mentioned_user_ids = (
                (self._bot_user_id,) if mentions_bot and self._bot_user_id else ()
            )
            if text.strip().startswith(mention):
                text = text.strip()[len(mention) :].strip()

        reply_to_data = msg_data.get("reply_to_message")
        reply_to = ""
        if reply_to_data and isinstance(reply_to_data, dict):
            reply_to = str(reply_to_data.get("message_id", ""))

        msg_date = msg_data.get("date")
        timestamp = msg_date if isinstance(msg_date, (int, float)) else time.time()

        attachments = self._extract_attachments(msg_data)
        sender_context = sender_context_from_values(
            display_name=self._sender_display_name(from_user),
            platform_user_id=str(from_user.get("id", "0")),
            is_bot=bool(from_user.get("is_bot", False)),
        )
        chat_context = chat_context_from_values(
            platform="telegram",
            chat_type="group" if is_group else "private",
            platform_chat_id=str(chat.get("id", "")),
            display_name=self._chat_display_name(chat),
        )

        inbound = InboundMessage(
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
            attachments=attachments,
            sender_context=sender_context,
            chat_context=chat_context,
            mentions_bot=mentions_bot,
            mentioned_user_ids=mentioned_user_ids,
        )
        return replace(inbound, message_context=context_from_inbound(inbound))

    @staticmethod
    def _sender_display_name(from_user: dict[str, Any]) -> str:
        username = str(from_user.get("username") or "").strip()
        if username:
            return f"@{username}"
        names = [
            str(from_user.get("first_name") or "").strip(),
            str(from_user.get("last_name") or "").strip(),
        ]
        return " ".join(name for name in names if name)

    @staticmethod
    def _chat_display_name(chat: dict[str, Any]) -> str:
        title = str(chat.get("title") or "").strip()
        if title:
            return title
        username = str(chat.get("username") or "").strip()
        if username:
            return f"@{username}"
        return str(chat.get("first_name") or "").strip()

    @staticmethod
    def _extract_attachments(msg_data: dict[str, Any]) -> list[InboundAttachment]:
        """Extract InboundAttachment objects from Telegram media fields."""
        attachments: list[InboundAttachment] = []

        photos = msg_data.get("photo")
        if isinstance(photos, list) and photos:
            largest = photos[-1]
            if isinstance(largest, dict):
                attachments.append(
                    InboundAttachment(
                        kind="image",
                        platform_id=str(largest.get("file_id", "")),
                        width=_safe_int(largest.get("width")),
                        height=_safe_int(largest.get("height")),
                    )
                )

        sticker = msg_data.get("sticker")
        if isinstance(sticker, dict) and not photos:
            attachments.append(
                InboundAttachment(
                    kind="image",
                    platform_id=str(sticker.get("file_id", "")),
                    alt_text=sticker.get("emoji", ""),
                    metadata={"sticker": True},
                )
            )

        doc = msg_data.get("document")
        if isinstance(doc, dict):
            attachments.append(
                InboundAttachment(
                    kind="file",
                    platform_id=str(doc.get("file_id", "")),
                    mime_type=str(doc.get("mime_type", "")),
                    file_size=_safe_int(doc.get("file_size")),
                    metadata={"file_name": doc.get("file_name", "")},
                )
            )

        video = msg_data.get("video")
        if isinstance(video, dict):
            attachments.append(
                InboundAttachment(
                    kind="video",
                    platform_id=str(video.get("file_id", "")),
                    mime_type=str(video.get("mime_type", "")),
                    file_size=_safe_int(video.get("file_size")),
                    width=_safe_int(video.get("width")),
                    height=_safe_int(video.get("height")),
                )
            )

        audio = msg_data.get("audio")
        if isinstance(audio, dict):
            attachments.append(
                InboundAttachment(
                    kind="audio",
                    platform_id=str(audio.get("file_id", "")),
                    mime_type=str(audio.get("mime_type", "")),
                    file_size=_safe_int(audio.get("file_size")),
                    metadata={"file_name": audio.get("file_name", "")},
                )
            )

        voice = msg_data.get("voice")
        if isinstance(voice, dict):
            attachments.append(
                InboundAttachment(
                    kind="audio",
                    platform_id=str(voice.get("file_id", "")),
                    mime_type=str(voice.get("mime_type", "")),
                    metadata={"duration": voice.get("duration", 0), "voice": True},
                )
            )

        animation = msg_data.get("animation")
        if isinstance(animation, dict):
            attachments.append(
                InboundAttachment(
                    kind="video",
                    platform_id=str(animation.get("file_id", "")),
                    mime_type=str(animation.get("mime_type", "")),
                    metadata={"animation": True},
                )
            )

        return attachments


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
