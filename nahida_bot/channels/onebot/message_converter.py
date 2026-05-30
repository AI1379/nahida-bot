"""OneBot message converter: segment array ↔ InboundMessage / OutboundMessage."""

from __future__ import annotations

import base64
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from nahida_bot.channels.onebot.config import OneBotPluginConfig
from nahida_bot.channels.onebot.segment_models import (
    FileSegment,
    ImageSegment,
    RecordSegment,
    ReplySegment,
    TextSegment,
    VideoSegment,
    find_segments,
    parse_segments,
    render_segments_plain_text,
    segments_to_array,
)
from nahida_bot.core.message_context import (
    chat_context_from_values,
    context_from_inbound,
    sender_context_from_values,
)
from nahida_bot.plugins.base import (
    Attachment,
    InboundAttachment,
    InboundMessage,
    OutboundMessage,
)


class OneBotMessageConverter:
    """Convert between OneBot message segments and nahida-bot message types."""

    def __init__(self, config: OneBotPluginConfig, *, self_id: str = "") -> None:
        self._config = config
        self._self_id = self_id

    @property
    def self_id(self) -> str:
        return self._self_id

    @self_id.setter
    def self_id(self, value: str) -> None:
        self._self_id = value

    # ── Inbound ──────────────────────────────────────────

    def to_inbound(
        self,
        raw_event: dict[str, Any],
        *,
        is_group: bool = False,
        chat_type: str = "private",
    ) -> InboundMessage | None:
        """Convert a v11 or v12 event dict to an InboundMessage."""
        segments = parse_segments(raw_event.get("message"))
        text = render_segments_plain_text(segments).strip() or str(
            raw_event.get("alt_message", "")
        )
        if not text:
            return None

        user_id = str(
            raw_event.get("user_id", raw_event.get("sender", {}).get("user_id", ""))
        )
        chat_id = _resolve_chat_id(raw_event, is_group)

        if not self._is_allowed(chat_type, chat_id):
            return None

        mentions_bot = self._has_self_mention(segments) or self._has_self_at_in_text(
            raw_event.get("raw_message", "")
        )
        mentioned_user_ids = self._extract_mentioned_user_ids(segments)

        visible_text = text
        if is_group and mentions_bot and self._self_id:
            visible_text = self._strip_self_mention_text(text)

        sender = raw_event.get("sender", {})
        if not isinstance(sender, dict):
            sender = {}
        sender_display_name = str(sender.get("card") or sender.get("nickname") or "")

        attachments = self._extract_attachments(segments)

        sender_context = sender_context_from_values(
            display_name=sender_display_name,
            platform_user_id=user_id,
            role_tags=self._sender_role_tags(sender, raw_event),
            is_self=bool(self._self_id and user_id == self._self_id),
        )
        chat_context = chat_context_from_values(
            platform="onebot",
            chat_type=chat_type,
            platform_chat_id=chat_id,
            display_name=self._chat_display_name(raw_event, is_group),
        )

        inbound = InboundMessage(
            message_id=str(raw_event.get("message_id", "")),
            platform="onebot",
            chat_id=chat_id,
            user_id=user_id,
            text=visible_text,
            raw_event=raw_event,
            is_group=is_group,
            reply_to=self._extract_reply_to(segments),
            timestamp=float(raw_event.get("time", 0)),
            command_prefix=self._config.command_prefix,
            attachments=attachments,
            sender_context=sender_context,
            chat_context=chat_context,
            mentions_bot=mentions_bot,
            mentioned_user_ids=mentioned_user_ids,
        )
        return replace(inbound, message_context=context_from_inbound(inbound))

    # ── Outbound ─────────────────────────────────────────

    def to_outbound_segments(self, message: OutboundMessage) -> list[dict[str, Any]]:
        """Convert an OutboundMessage to a OneBot segment array.

        Returns a list of dicts ready for ``send_msg``. Attachments that carry
        a local file path are automatically base64-encoded and inlined.
        """
        segments: list[Any] = []

        if message.reply_to:
            segments.append(ReplySegment(message_id=message.reply_to))

        text = message.text
        if text:
            max_len = self._config.max_text_length
            if self._config.split_long_text and len(text) > max_len:
                text = text[:max_len]
            segments.append(TextSegment(text=text))

        for attachment in message.attachments:
            seg = self._attachment_to_segment(attachment)
            if seg is not None:
                segments.append(seg)

        return segments_to_array(segments)

    def _attachment_to_segment(self, attachment: Attachment) -> Any | None:
        """Convert an outbound Attachment to a typed OneBot segment.

        Handles local file paths via base64 encoding, URLs as-is, and
        file_id references.
        """
        file_ref = self._resolve_file_ref(attachment)

        kind = attachment.type
        if kind in ("photo", "image"):
            return ImageSegment(file=file_ref)
        elif kind in ("audio", "voice"):
            return RecordSegment(file=file_ref)
        elif kind == "video":
            return VideoSegment(file=file_ref)
        elif kind in ("document", "file"):
            return FileSegment(
                file=file_ref,
                name=attachment.filename,
            )
        return None

    def _resolve_file_ref(self, attachment: Attachment) -> str:
        """Resolve an Attachment to a OneBot ``file`` string.

        Priority:
        1. If path is a local file that exists → base64-encode
        2. If path is a URL → return as-is
        3. If path is a file_id → return as-is
        """
        path = attachment.path
        if not path:
            return ""

        # URL
        if path.startswith(("http://", "https://")):
            return path

        # Already base64-encoded
        if path.startswith("base64://"):
            return path

        # Local file → base64
        file_path = Path(path)
        if file_path.is_file():
            try:
                data = file_path.read_bytes()
                b64 = base64.b64encode(data).decode("ascii")
                return f"base64://{b64}"
            except OSError:
                pass

        # Fallback: return as-is (may be a file_id string)
        return path

    # ── Inbound helpers ──────────────────────────────────

    def _is_allowed(self, chat_type: str, chat_id: str) -> bool:
        if chat_type == "private" and self._config.allowed_friends:
            return chat_id in self._config.allowed_friends
        if chat_type == "group" and self._config.allowed_groups:
            return chat_id in self._config.allowed_groups
        return True

    def _has_self_mention(self, segments: list[Any]) -> bool:
        if not self._self_id:
            return False
        for seg in find_segments(segments, "at"):
            qq = str(_seg_data(seg).get("qq", ""))
            if qq == self._self_id:
                return True
        for seg in find_segments(segments, "mention"):
            uid = str(_seg_data(seg).get("user_id", ""))
            if uid == self._self_id:
                return True
        return False

    def _has_self_at_in_text(self, raw_message: object) -> bool:
        if not self._self_id or not isinstance(raw_message, str):
            return False
        return f"[CQ:at,qq={self._self_id}]" in raw_message

    def _extract_mentioned_user_ids(self, segments: list[Any]) -> tuple[str, ...]:
        ids: list[str] = []
        for seg in find_segments(segments, "at"):
            qq = str(_seg_data(seg).get("qq", ""))
            if qq and qq != "all":
                ids.append(qq)
        for seg in find_segments(segments, "mention"):
            uid = str(_seg_data(seg).get("user_id", ""))
            if uid:
                ids.append(uid)
        return tuple(dict.fromkeys(ids))

    def _strip_self_mention_text(self, text: str) -> str:
        import re

        if not self._self_id:
            return text
        text = re.sub(re.escape(f"[CQ:at,qq={self._self_id}]"), "", text)
        text = re.sub(rf"@\[qq={re.escape(self._self_id)}\]\s*", "", text)
        text = re.sub(rf"@\[user_id={re.escape(self._self_id)}\]\s*", "", text)
        return text.strip()

    def _extract_reply_to(self, segments: list[Any]) -> str:
        for seg in find_segments(segments, "reply"):
            data = _seg_data(seg)
            return str(data.get("id", data.get("message_id", "")))
        return ""

    def _extract_attachments(self, segments: list[Any]) -> list[InboundAttachment]:
        """Extract first-class InboundAttachment objects from media segments."""
        attachments: list[InboundAttachment] = []

        for seg in find_segments(segments, "image"):
            data = _seg_data(seg)
            attachments.append(
                InboundAttachment(
                    kind="image",
                    platform_id=str(data.get("file_id", data.get("file", ""))),
                    url=str(data.get("url", "")),
                    alt_text=str(data.get("summary", "")),
                    metadata={
                        "sub_type": str(data.get("sub_type") or data.get("type", "")),
                        "file": str(data.get("file", "")),
                    },
                )
            )

        for seg in find_segments(segments, "record"):
            data = _seg_data(seg)
            attachments.append(
                InboundAttachment(
                    kind="audio",
                    platform_id=str(data.get("file_id", data.get("file", ""))),
                    url=str(data.get("url", "")),
                    metadata={"duration": data.get("duration", 0)},
                )
            )

        for seg in find_segments(segments, "voice"):
            data = _seg_data(seg)
            attachments.append(
                InboundAttachment(
                    kind="audio",
                    platform_id=str(data.get("file_id", data.get("file", ""))),
                    url=str(data.get("url", "")),
                    metadata={"duration": data.get("duration", 0)},
                )
            )

        for seg in find_segments(segments, "video"):
            data = _seg_data(seg)
            attachments.append(
                InboundAttachment(
                    kind="video",
                    platform_id=str(data.get("file_id", data.get("file", ""))),
                    url=str(data.get("url", "")),
                    width=int(data.get("width", 0)),
                    height=int(data.get("height", 0)),
                    metadata={"duration": data.get("duration", 0)},
                )
            )

        for seg in find_segments(segments, "file"):
            data = _seg_data(seg)
            attachments.append(
                InboundAttachment(
                    kind="file",
                    platform_id=str(data.get("file_id", data.get("file", ""))),
                    file_size=int(data.get("file_size", 0)),
                    metadata={
                        "file_name": str(data.get("file_name") or data.get("name", "")),
                        "file_size": int(data.get("file_size", 0)),
                    },
                )
            )

        return attachments

    @staticmethod
    def _sender_role_tags(
        sender: dict[str, Any], raw_event: dict[str, Any]
    ) -> tuple[str, ...]:
        tags: list[str] = []
        role = str(sender.get("role", "")).lower()
        if role in {"owner", "admin", "administrator"}:
            tags.append("owner" if role == "owner" else "admin")
        return tuple(dict.fromkeys(tags))

    @staticmethod
    def _chat_display_name(raw_event: dict[str, Any], is_group: bool) -> str:
        if is_group:
            return str(
                raw_event.get("group_name", "")
                or raw_event.get("group", {}).get("group_name", "")
            )
        sender = raw_event.get("sender", {})
        if isinstance(sender, dict):
            return str(sender.get("nickname", ""))
        return ""


# ── Standalone helpers ───────────────────────────────────


def _resolve_chat_id(raw_event: dict[str, Any], is_group: bool) -> str:
    if is_group:
        return str(raw_event.get("group_id", ""))
    return str(raw_event.get("user_id", raw_event.get("sender", {}).get("user_id", "")))


def _seg_data(seg: Any) -> dict[str, Any]:
    """Extract data dict from any segment representation."""
    if hasattr(seg, "data") and not callable(getattr(seg, "data")):
        data = getattr(seg, "data")
        if isinstance(data, dict):
            return data
    if isinstance(seg, dict):
        data = seg.get("data")
        if isinstance(data, dict):
            return data
    return {}


def _guess_mime(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".ogg": "audio/ogg",
        ".amr": "audio/amr",
        ".silk": "audio/silk",
        ".mp4": "video/mp4",
        ".flv": "video/x-flv",
        ".mov": "video/quicktime",
        ".pdf": "application/pdf",
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".zip": "application/zip",
        ".txt": "text/plain",
    }.get(ext, "application/octet-stream")
