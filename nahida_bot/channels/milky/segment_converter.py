"""Milky outbound message conversion."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, cast

from nahida_bot.channels.milky.config import MilkyPluginConfig
from nahida_bot.channels.milky.segments import (
    OutgoingFileUpload,
    OutgoingForwardSegment,
    OutgoingForwardedMessage,
    OutgoingImageSegment,
    OutgoingRecordSegment,
    OutgoingReplySegment,
    OutgoingSegment,
    OutgoingTextSegment,
    OutgoingVideoSegment,
)
from nahida_bot.plugins.base import Attachment, OutboundMessage

MessageScene = Literal["friend", "group"]


class MilkyTargetError(ValueError):
    """Raised when a Milky send target cannot be resolved."""


class MilkyOutboundConverter:
    """Convert ``OutboundMessage`` into Milky message/file payloads."""

    def __init__(self, config: MilkyPluginConfig) -> None:
        self._config = config

    def to_payload(
        self, message: OutboundMessage
    ) -> tuple[list[OutgoingSegment], list[OutgoingFileUpload]]:
        """Return message segments and file uploads for one outbound message."""
        segments: list[OutgoingSegment] = []
        files: list[OutgoingFileUpload] = []

        if message.reply_to:
            reply_seq = _parse_int(message.reply_to)
            if reply_seq is not None:
                segments.append(OutgoingReplySegment(reply_seq))

        for chunk in self._split_text(message.text):
            segments.append(OutgoingTextSegment(chunk))

        for attachment in message.attachments:
            media_segment = self._attachment_to_media_segment(attachment)
            if media_segment is not None:
                segments.append(media_segment)
            else:
                files.append(self._attachment_to_file_upload(attachment))

        segments.extend(_extra_segments(message.extra))
        return segments, files

    def _split_text(self, text: str) -> list[str]:
        if not text:
            return []
        max_len = self._config.max_text_length
        return [text[i : i + max_len] for i in range(0, len(text), max_len)]

    def _attachment_to_media_segment(
        self, attachment: Attachment
    ) -> OutgoingSegment | None:
        uri = _attachment_uri(attachment)
        if attachment.type in {"photo", "image"}:
            return OutgoingImageSegment(uri=uri, summary=attachment.caption)
        if attachment.type in {"audio", "voice", "record"}:
            return OutgoingRecordSegment(uri=uri)
        if attachment.type == "video":
            return OutgoingVideoSegment(uri=uri)
        return None

    @staticmethod
    def _attachment_to_file_upload(attachment: Attachment) -> OutgoingFileUpload:
        path = Path(attachment.path)
        return OutgoingFileUpload(
            file_uri=_attachment_uri(attachment),
            file_name=attachment.filename or path.name,
        )


def resolve_target(
    target: str,
    message: OutboundMessage,
    *,
    scene_by_peer: dict[str, str] | None = None,
) -> tuple[MessageScene, int]:
    """Resolve Milky message scene and peer ID from target/message metadata."""
    extra_scene = message.extra.get("message_scene")
    extra_peer = message.extra.get("peer_id")
    if extra_scene in {"friend", "group"} and extra_peer is not None:
        return cast(MessageScene, extra_scene), _parse_peer_id(extra_peer)

    if ":" in target:
        prefix, value = target.split(":", 1)
        if prefix in {"friend", "group"}:
            return cast(MessageScene, prefix), _parse_peer_id(value)

    scene = (scene_by_peer or {}).get(target)
    if scene in {"friend", "group"}:
        return cast(MessageScene, scene), _parse_peer_id(target)

    if extra_scene in {"friend", "group"}:
        return cast(MessageScene, extra_scene), _parse_peer_id(target)

    return "friend", _parse_peer_id(target)


def message_seq_from_send_result(result: dict[str, object]) -> str:
    """Extract a platform message ID from Milky send/upload result data."""
    for key in ("message_seq", "file_id"):
        value = result.get(key)
        if value is not None:
            return str(value)
    return ""


def fallback_text_for_segments(segments: list[OutgoingSegment]) -> str:
    """Return a readable fallback when Milky cannot send rich segments."""
    parts: list[str] = []
    for segment in segments:
        if isinstance(segment, OutgoingTextSegment):
            parts.append(segment.text)
        elif isinstance(segment, OutgoingImageSegment):
            parts.append(f"[Image: {segment.uri}]")
        elif isinstance(segment, OutgoingRecordSegment):
            parts.append(f"[Voice: {segment.uri}]")
        elif isinstance(segment, OutgoingVideoSegment):
            parts.append(f"[Video: {segment.uri}]")
        elif isinstance(segment, OutgoingForwardSegment):
            parts.append(_fallback_text_for_forward(segment))
    return "\n".join(part for part in parts if part).strip()


def has_rich_segments(segments: list[OutgoingSegment]) -> bool:
    """Whether segments include non-text content likely to need fallback."""
    return any(
        not isinstance(segment, (OutgoingTextSegment, OutgoingReplySegment))
        for segment in segments
    )


def _attachment_uri(attachment: Attachment) -> str:
    path = attachment.path
    if path.startswith(("file://", "http://", "https://")):
        return path
    return Path(path).resolve().as_uri()


def _parse_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _parse_peer_id(value: object) -> int:
    try:
        return int(str(value))
    except ValueError as exc:
        raise MilkyTargetError(f"Invalid Milky target peer id: {value!r}") from exc


def _extra_segments(extra: dict[str, object]) -> list[OutgoingSegment]:
    segments: list[OutgoingSegment] = []

    raw_segments = extra.get("milky_segments")
    if isinstance(raw_segments, list):
        for raw in raw_segments:
            if isinstance(raw, dict):
                converted = _segment_from_dict(raw)
                if converted is not None:
                    segments.append(converted)

    raw_forward = extra.get("milky_forward")
    if isinstance(raw_forward, dict):
        forward = _forward_from_dict(raw_forward)
        if forward is not None:
            segments.append(forward)

    return segments


def _segment_from_dict(raw: dict[str, object]) -> OutgoingSegment | None:
    segment_type = raw.get("type")
    data = raw.get("data")
    if not isinstance(data, dict):
        data = {}

    if segment_type == "text":
        return OutgoingTextSegment(str(data.get("text", "")))
    if segment_type == "reply":
        message_seq = _parse_int(str(data.get("message_seq", "")))
        return OutgoingReplySegment(message_seq) if message_seq is not None else None
    if segment_type == "image":
        uri = str(data.get("uri", ""))
        return OutgoingImageSegment(uri=uri) if uri else None
    if segment_type == "record":
        uri = str(data.get("uri", ""))
        return OutgoingRecordSegment(uri=uri) if uri else None
    if segment_type == "video":
        uri = str(data.get("uri", ""))
        return OutgoingVideoSegment(uri=uri) if uri else None
    if segment_type == "forward":
        return _forward_from_dict(data)
    return None


def _forward_from_dict(raw: dict[str, object]) -> OutgoingForwardSegment | None:
    messages_raw = raw.get("messages")
    if not isinstance(messages_raw, list):
        return None

    messages: list[OutgoingForwardedMessage] = []
    for item in messages_raw:
        if not isinstance(item, dict):
            continue
        user_id = _parse_int(str(item.get("user_id", "0"))) or 0
        sender_name = str(item.get("sender_name", ""))
        item_segments = _forward_message_segments(item)
        if sender_name and item_segments:
            messages.append(
                OutgoingForwardedMessage(
                    user_id=user_id,
                    sender_name=sender_name,
                    segments=item_segments,
                )
            )

    if not messages:
        return None
    preview_raw = raw.get("preview")
    return OutgoingForwardSegment(
        messages=messages,
        title=str(raw.get("title", "")),
        preview=[str(value) for value in preview_raw]
        if isinstance(preview_raw, list)
        else [],
        summary=str(raw.get("summary", "")),
        prompt=str(raw.get("prompt", "")),
    )


def _forward_message_segments(raw: dict[str, object]) -> list[OutgoingSegment]:
    segments: list[OutgoingSegment] = []
    raw_segments = raw.get("segments")
    if isinstance(raw_segments, list):
        for item in raw_segments:
            if isinstance(item, dict):
                segment = _segment_from_dict(item)
                if segment is not None:
                    segments.append(segment)

    text = raw.get("text")
    if isinstance(text, str) and text:
        segments.insert(0, OutgoingTextSegment(text))
    return segments


def _fallback_text_for_forward(segment: OutgoingForwardSegment) -> str:
    lines = [segment.title or "[Forward]"]
    for message in segment.messages:
        content = fallback_text_for_segments(message.segments)
        lines.append(f"- {message.sender_name}: {content}")
    return "\n".join(lines)
