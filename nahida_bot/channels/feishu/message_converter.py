"""Feishu inbound event conversion (im.message.receive_v1 → InboundMessage)."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from nahida_bot.channels.feishu._parsing import as_mapping, coerce_int, coerce_str
from nahida_bot.channels.feishu.config import FeishuPluginConfig
from nahida_bot.core.message_context import (
    chat_context_from_values,
    context_from_inbound,
    sender_context_from_values,
)
from nahida_bot.plugins.base import InboundAttachment, InboundMessage

# Resolve (chat_id, open_id) → display name; provided by the plugin via the
# chat-members cache. Returns "" when unknown.
NameResolver = Callable[[str, str], Awaitable[str]]

RECEIVE_EVENT_TYPE = "im.message.receive_v1"


class FeishuMessageConverter:
    """Convert one Feishu ``im.message.receive_v1`` event into ``InboundMessage``."""

    def __init__(
        self,
        config: FeishuPluginConfig,
        *,
        self_open_id: str = "",
        name_resolver: NameResolver | None = None,
        observe_untriggered_group_messages: bool = False,
    ) -> None:
        self._config = config
        self._self_open_id = self_open_id
        self._name_resolver = name_resolver
        self._observe_untriggered_group_messages = observe_untriggered_group_messages

    async def to_inbound(self, event: dict[str, Any]) -> InboundMessage | None:
        """Convert one receive event, returning None when filtered out."""
        header = as_mapping(event.get("header"))
        if coerce_str(header.get("event_type")) != RECEIVE_EVENT_TYPE:
            return None
        body = as_mapping(event.get("event"))
        sender = as_mapping(body.get("sender"))
        sender_id = as_mapping(sender.get("sender_id"))
        message = as_mapping(body.get("message"))

        chat_id = coerce_str(message.get("chat_id"))
        message_id = coerce_str(message.get("message_id"))
        sender_open_id = coerce_str(sender_id.get("open_id"))
        if not chat_id or not message_id:
            return None

        is_group = coerce_str(message.get("chat_type")) == "group"
        if not self._is_allowed(is_group, chat_id, sender_open_id):
            return None

        mentions = self._parse_mentions(message.get("mentions"))
        mentions_bot = self._mentions_bot(mentions)
        content = _parse_content(message.get("content"))
        message_type = coerce_str(message.get("message_type"))

        text = self._render_text(message_type, content, mentions, mentions_bot)
        attachments = _extract_attachments(message_type, content, message_id)

        # A message with neither text nor downloadable attachments carries no
        # signal for the agent (e.g. an undownloadable sticker).
        if not text and not attachments:
            return None

        display_name = await self._resolve_display_name(
            is_group, chat_id, sender_open_id, mentions
        )
        sender_context = sender_context_from_values(
            display_name=display_name,
            platform_user_id=sender_open_id,
            is_bot=coerce_str(sender.get("sender_type")) == "bot",
            is_self=bool(self._self_open_id) and sender_open_id == self._self_open_id,
        )
        chat_context = chat_context_from_values(
            platform="feishu",
            chat_type="group" if is_group else "private",
            platform_chat_id=chat_id,
        )

        inbound = InboundMessage(
            message_id=message_id,
            platform="feishu",
            chat_id=chat_id,
            user_id=sender_open_id,
            text=text,
            raw_event=event,
            is_group=is_group,
            reply_to=coerce_str(message.get("parent_id"))
            or coerce_str(message.get("root_id")),
            timestamp=_message_timestamp(message.get("create_time")),
            command_prefix=self._config.command_prefix,
            attachments=attachments,
            sender_context=sender_context,
            chat_context=chat_context,
            mentions_bot=mentions_bot,
            mentioned_user_ids=tuple(
                mention.open_id for mention in mentions if mention.open_id
            ),
        )
        return replace(inbound, message_context=context_from_inbound(inbound))

    # ── mention helpers ───────────────────────────────────────────

    def _parse_mentions(self, raw: object) -> list[_Mention]:
        mentions: list[_Mention] = []
        if not isinstance(raw, list):
            return mentions
        for item in raw:
            if not isinstance(item, dict):
                continue
            ids = as_mapping(item.get("id"))
            mentions.append(
                _Mention(
                    key=coerce_str(item.get("key")),
                    open_id=coerce_str(ids.get("open_id")),
                    name=coerce_str(item.get("name")),
                    is_bot=coerce_str(item.get("mentioned_type")) == "bot",
                )
            )
        return mentions

    def _mentions_bot(self, mentions: list[_Mention]) -> bool:
        if self._self_open_id:
            return any(mention.open_id == self._self_open_id for mention in mentions)
        # Self identity not yet resolved. With the group_at_msg scope the only
        # bot the platform delivers @-mentions for is this bot, so a bot-typed
        # mention is a safe-enough trigger signal until bot/v3/info lands.
        return any(mention.is_bot for mention in mentions)

    def _mention_label(self, mention: _Mention) -> str:
        return mention.name or mention.open_id

    def _replace_mention_placeholders(
        self,
        text: str,
        mentions: list[_Mention],
        mentions_bot: bool,
    ) -> str:
        """Rewrite ``@_user_N`` placeholders; strip the self-mention."""
        for mention in mentions:
            if not mention.key:
                continue
            replacement = (
                ""
                if (mentions_bot and mention.open_id == self._self_open_id)
                else (f"@{self._mention_label(mention)}")
            )
            text = text.replace(mention.key, replacement)
        # Collapse the double spaces left around a stripped self-mention.
        if mentions_bot and self._self_open_id:
            text = re.sub(r" {2,}", " ", text)
        return text

    # ── content rendering ─────────────────────────────────────────

    def _render_text(
        self,
        message_type: str,
        content: dict[str, Any],
        mentions: list[_Mention],
        mentions_bot: bool,
    ) -> str:
        if message_type == "text":
            raw = coerce_str(content.get("text"))
            return self._replace_mention_placeholders(
                raw, mentions, mentions_bot
            ).strip()

        if message_type == "post":
            paragraphs = _post_paragraphs(content)
            lines: list[str] = []
            for paragraph in paragraphs:
                pieces: list[str] = []
                for element in paragraph:
                    pieces.append(self._render_post_element(element, mentions))
                lines.append("".join(pieces))
            stripped_lines = (line.strip() for line in lines)
            return "\n".join(line for line in stripped_lines if line)

        return _content_placeholder(message_type, content)

    def _render_post_element(
        self, element: dict[str, Any], mentions: list[_Mention]
    ) -> str:
        tag = coerce_str(element.get("tag"))
        if tag == "text":
            return coerce_str(element.get("text"))
        if tag == "a":
            text = coerce_str(element.get("text"))
            href = coerce_str(element.get("href"))
            return f"{text}({href})" if href and href not in text else text
        if tag == "at":
            user_id = coerce_str(element.get("user_id"))
            name = coerce_str(element.get("user_name"))
            if not name and user_id:
                name = next(
                    (m.name for m in mentions if m.open_id == user_id),
                    "",
                )
            if user_id == "all":
                return "@所有人"
            return f"@{name or user_id}"
        if tag == "img":
            return "[图片]"
        if tag == "media":
            return "[视频]"
        if tag == "emotion":
            return "[表情]"
        return ""

    # ── misc ──────────────────────────────────────────────────────

    def _is_allowed(self, is_group: bool, chat_id: str, sender_open_id: str) -> bool:
        if self._config.allowed_chats and chat_id not in self._config.allowed_chats:
            return False
        if not is_group and self._config.allowed_users:
            return sender_open_id in self._config.allowed_users
        return True

    async def _resolve_display_name(
        self,
        is_group: bool,
        chat_id: str,
        sender_open_id: str,
        mentions: list[_Mention],
    ) -> str:
        # The receive event carries no sender name. If the sender happens to
        # be mentioned in their own message the name is already known.
        for mention in mentions:
            if mention.open_id == sender_open_id and mention.name:
                return mention.name
        if self._name_resolver is not None:
            try:
                name = await self._name_resolver(chat_id, sender_open_id)
            except Exception:  # noqa: BLE001 - name lookup must never break receive
                name = ""
            if name:
                return name
        # Readable fallback: keep the ou_ prefix plus the trailing 6 chars so
        # the id stays unambiguous for the identity system but less noisy.
        return (
            sender_open_id
            if len(sender_open_id) <= 10
            else sender_open_id[:6] + "…" + sender_open_id[-4:]
        )


class _Mention:
    __slots__ = ("key", "open_id", "name", "is_bot")

    def __init__(self, *, key: str, open_id: str, name: str, is_bot: bool) -> None:
        self.key = key
        self.open_id = open_id
        self.name = name
        self.is_bot = is_bot


def _parse_content(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _post_paragraphs(content: dict[str, Any]) -> list[list[dict[str, Any]]]:
    post = content.get("post")
    if not isinstance(post, dict):
        return []
    body = None
    for key in ("zh_cn", "en_us", "ja_jp"):
        if isinstance(post.get(key), dict):
            body = post[key]
            break
    if body is None:
        for value in post.values():
            if isinstance(value, dict) and isinstance(value.get("content"), list):
                body = value
                break
    if body is None:
        return []
    paragraphs = body.get("content")
    if not isinstance(paragraphs, list):
        return []
    result: list[list[dict[str, Any]]] = []
    for paragraph in paragraphs:
        if isinstance(paragraph, list):
            result.append(
                [element for element in paragraph if isinstance(element, dict)]
            )
    return result


def _extract_attachments(
    message_type: str, content: dict[str, Any], message_id: str
) -> list[InboundAttachment]:
    """Extract media attachments; ``platform_id`` encodes message_id:file_key."""
    attachments: list[InboundAttachment] = []

    def _attachment(
        kind: str,
        file_key: str,
        *,
        file_name: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> InboundAttachment:
        meta = {
            "resource_type": "file" if kind != "image" else "image",
            **(metadata or {}),
        }
        return InboundAttachment(
            kind=kind,
            platform_id=f"{message_id}:{file_key}",
            file_size=coerce_int(meta.get("file_size"), default=0),
            alt_text=file_name,
            metadata={**meta, "file_key": file_key, "message_id": message_id},
        )

    if message_type == "image":
        image_key = coerce_str(content.get("image_key"))
        if image_key:
            attachments.append(
                _attachment("image", image_key, metadata={"resource_type": "image"})
            )
    elif message_type == "file":
        file_key = coerce_str(content.get("file_key"))
        if file_key:
            attachments.append(
                _attachment(
                    "file", file_key, file_name=coerce_str(content.get("file_name"))
                )
            )
    elif message_type == "audio":
        file_key = coerce_str(content.get("file_key"))
        if file_key:
            attachments.append(
                _attachment(
                    "audio",
                    file_key,
                    metadata={"duration": coerce_int(content.get("duration"))},
                )
            )
    elif message_type == "media":
        file_key = coerce_str(content.get("file_key"))
        if file_key:
            attachments.append(
                _attachment(
                    "video",
                    file_key,
                    metadata={"image_key": coerce_str(content.get("image_key"))},
                )
            )
    elif message_type == "post":
        for paragraph in _post_paragraphs(content):
            for element in paragraph:
                tag = coerce_str(element.get("tag"))
                if tag == "img":
                    image_key = coerce_str(element.get("image_key"))
                    if image_key:
                        attachments.append(
                            _attachment(
                                "image", image_key, metadata={"resource_type": "image"}
                            )
                        )
                elif tag == "media":
                    file_key = coerce_str(element.get("file_key"))
                    if file_key:
                        attachments.append(_attachment("video", file_key))
    return attachments


def _content_placeholder(message_type: str, content: dict[str, Any]) -> str:
    if message_type == "sticker":
        return "[表情包]"
    if message_type == "share_chat":
        return "[群名片分享]"
    if message_type == "share_user":
        return "[个人名片分享]"
    if message_type == "system":
        return coerce_str(content.get("text"))
    return ""


def _message_timestamp(raw: object) -> float:
    """Feishu create_time is epoch milliseconds as a string."""
    value = coerce_int(raw, default=0)
    if value <= 0:
        return 0.0
    # Modern events are milliseconds; tolerate seconds for safety.
    return value / 1000.0 if value > 10_000_000_000 else float(value)
