"""Discord message dict ↔ InboundMessage conversion.

The transport layer (``transport.py``) flattens every ``discord.Message``
into a plain dict; this converter owns all semantics: chat classification,
ChatAddress target types, mention handling, and attachment normalization.

Address model (see docs/design/chat-address-and-session-id.md):

- DM                       → ``discord:private:<dm_channel_id>``
- Guild text channel       → ``discord:channel:<channel_id>`` (``is_group``)
- Thread / forum post      → ``discord:thread:<thread_id>``   (``is_group``)

Discord snowflakes are globally unique, so a thread id is a sufficient
``target_id`` and the router's ``ChatAddress.from_inbound`` round-trips it
without core changes. The 4-segment ``channel:thread:<parent>:<thread>``
form stays reserved for platforms whose topic ids are only unique within
a container (e.g. GitHub issue numbers within a repo).
"""

from __future__ import annotations

import re
import time
from collections import OrderedDict
from dataclasses import replace
from typing import Any

from nahida_bot.core.message_context import (
    chat_context_from_values,
    context_from_inbound,
    sender_context_from_values,
)
from nahida_bot.plugins.base import InboundAttachment, InboundMessage

# Channel type names (discord.ChannelType.name) that are threads. Forum
# posts arrive as public threads, so they follow the same path.
THREAD_CHANNEL_TYPES = frozenset({"public_thread", "private_thread"})
# Message.type names that carry user-authored content.
CONTENT_MESSAGE_TYPES = frozenset({"default", "reply"})

# Raw user mention tokens: <@123> and the legacy <@!123> form.
_MENTION_TOKEN_RE = re.compile(r"<@!?(\d+)>")

KIND_BY_MIME_PREFIX = (
    ("image/", "image"),
    ("video/", "video"),
    ("audio/", "audio"),
    ("text/", "file"),
)


def classify_chat(message: dict[str, Any]) -> tuple[str, bool]:
    """Return ``(chat_type, is_group)`` for a message dict.

    ``chat_type`` is one of ``private`` / ``channel`` / ``thread`` and is
    used both as the ChatAddress target type and ChatContext.chat_type.
    Group DMs (rare, bot added to a group) are treated as private chats.
    """
    channel = message.get("channel") or {}
    channel_type = str(channel.get("type", ""))
    in_guild = bool(message.get("guild_id"))
    if channel_type in THREAD_CHANNEL_TYPES:
        return "thread", True
    if in_guild:
        return "channel", True
    return "private", False


class DiscordMessageConverter:
    """Converts Discord message dicts (transport payload) to InboundMessage."""

    def __init__(
        self,
        bot_user_id: str = "",
        bot_username: str = "",
    ) -> None:
        self._bot_user_id = bot_user_id
        self._bot_username = bot_username

    @property
    def bot_user_id(self) -> str:
        return self._bot_user_id

    @property
    def bot_username(self) -> str:
        return self._bot_username

    def to_inbound(self, message: dict[str, Any]) -> InboundMessage:
        """Convert a transport message dict to InboundMessage."""
        channel = message.get("channel") or {}
        author = message.get("author") or {}
        chat_type, is_group = classify_chat(message)

        mentions = [
            {
                "id": str(mention.get("id", "")),
                "name": str(mention.get("name", "")),
            }
            for mention in (message.get("mentions") or [])
            if isinstance(mention, dict)
        ]
        mentioned_ids = {mention["id"] for mention in mentions if mention["id"]}
        mentions_bot = bool(self._bot_user_id) and self._bot_user_id in mentioned_ids

        text = self._rewrite_mentions(str(message.get("content", "")), mentions)
        attachments = self._extract_attachments(message.get("attachments") or [])
        marker_lines = [
            f"[Attachment: name={a.metadata.get('file_name', '')}, "
            f"type={a.kind}, id={a.platform_id}]"
            for a in attachments
        ]
        if marker_lines:
            text = f"{text}\n{'\n'.join(marker_lines)}".strip()

        reply_to = str(message.get("reference_message_id", "") or "")
        timestamp = message.get("timestamp")
        ts = float(timestamp) if isinstance(timestamp, (int, float)) else time.time()

        chat_id = str(channel.get("id", ""))
        sender_context = sender_context_from_values(
            display_name=self._sender_display_name(author),
            platform_user_id=str(author.get("id", "0")),
            is_bot=bool(author.get("bot", False)),
        )
        chat_context = chat_context_from_values(
            platform="discord",
            chat_type=chat_type,
            platform_chat_id=chat_id,
            display_name=self._chat_display_name(channel, chat_type),
        )

        inbound = InboundMessage(
            message_id=str(message.get("id", "0")),
            platform="discord",
            chat_id=chat_id,
            user_id=str(author.get("id", "0")),
            text=text,
            raw_event=message,
            is_group=is_group,
            reply_to=reply_to,
            timestamp=ts,
            command_prefix="/",
            attachments=attachments,
            sender_context=sender_context,
            chat_context=chat_context,
            mentions_bot=mentions_bot,
            mentioned_user_ids=tuple(sorted(mentioned_ids)),
        )
        return replace(inbound, message_context=context_from_inbound(inbound))

    # ── Internals ─────────────────────────────────────────

    def _rewrite_mentions(self, content: str, mentions: list[dict[str, str]]) -> str:
        """Rewrite ``<@id>`` tokens to readable ``@name`` and strip a leading
        bot mention so ``<@bot> /help`` becomes ``/help``."""
        text = content.strip()
        if not text:
            return text
        # Strip the bot's own raw token first, before rewriting: after the
        # rewrite the id is gone and only the display name remains.
        if self._bot_user_id:
            token_re = re.compile(rf"^<@!?{re.escape(self._bot_user_id)}>\s*")
            text = token_re.sub("", text)

        def _display(mention_id: str) -> str:
            for mention in mentions:
                if mention["id"] == mention_id:
                    return mention["name"] or mention_id
            return mention_id

        return _MENTION_TOKEN_RE.sub(
            lambda match: f"@{_display(match.group(1))}", text
        ).strip()

    @staticmethod
    def _sender_display_name(author: dict[str, Any]) -> str:
        return (
            str(author.get("display_name") or "").strip()
            or str(author.get("name") or "").strip()
            or str(author.get("id", "")).strip()
        )

    @staticmethod
    def _chat_display_name(channel: dict[str, Any], chat_type: str) -> str:
        name = str(channel.get("name") or "").strip()
        parent_id = str(channel.get("parent_id") or "").strip()
        if chat_type == "private":
            return name
        if chat_type == "thread" and name:
            return name if not parent_id else f"{name} (in #{parent_id})"
        if name:
            return f"#{name}"
        return str(channel.get("id", "")).strip()

    @staticmethod
    def _extract_attachments(
        raw_attachments: list[Any],
    ) -> list[InboundAttachment]:
        attachments: list[InboundAttachment] = []
        for raw in raw_attachments:
            if not isinstance(raw, dict):
                continue
            mime_type = str(raw.get("content_type", "") or "")
            kind = "file"
            for prefix, mapped in KIND_BY_MIME_PREFIX:
                if mime_type.startswith(prefix):
                    kind = mapped
                    break
            filename = str(raw.get("filename", "") or "")
            attachments.append(
                InboundAttachment(
                    kind=kind,
                    platform_id=str(raw.get("id", "")),
                    url=str(raw.get("url", "") or ""),
                    mime_type=mime_type,
                    file_size=_safe_int(raw.get("size")),
                    width=_safe_int(raw.get("width")),
                    height=_safe_int(raw.get("height")),
                    metadata={
                        "file_name": filename,
                        "spoiler": bool(raw.get("spoiler", False)),
                    },
                )
            )
        return attachments


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class AttachmentUrlCache:
    """Bounded map of attachment id → download URL.

    Discord attachment URLs are signed and expire; ``download_media`` needs
    the URL captured at receive time because there is no fetch-by-id API.
    Pure dict logic — no discord.py dependency.
    """

    def __init__(self, capacity: int = 1024) -> None:
        self._entries: OrderedDict[str, dict[str, str]] = OrderedDict()
        self._capacity = capacity

    def record(self, attachments: list[Any]) -> None:
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            attachment_id = str(attachment.get("id", ""))
            url = str(attachment.get("url", ""))
            if not attachment_id or not url:
                continue
            self._entries[attachment_id] = {
                "url": url,
                "filename": str(attachment.get("filename", "")),
                "content_type": str(attachment.get("content_type", "")),
            }
            self._entries.move_to_end(attachment_id)
        while len(self._entries) > self._capacity:
            self._entries.popitem(last=False)

    def get(self, attachment_id: str) -> dict[str, str] | None:
        entry = self._entries.get(attachment_id)
        if entry is None:
            return None
        self._entries.move_to_end(attachment_id)
        return dict(entry)
