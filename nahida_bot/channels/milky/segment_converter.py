"""Milky outbound message conversion."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, cast
from urllib.parse import unquote, urlparse

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
from nahida_bot.core.chat_address import ChatAddress, normalize_target_type
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

    extra_address = _chat_address_from_extra(message.extra.get("chat_address"))
    if extra_address is not None:
        return _scene_and_peer_from_address(extra_address)

    target_address = _chat_address_from_target(target)
    if target_address is not None:
        return _scene_and_peer_from_address(target_address)

    if ":" in target:
        prefix, value = target.split(":", 1)
        if prefix in {"friend", "group"}:
            return cast(MessageScene, prefix), _parse_peer_id(value)

    scene = (scene_by_peer or {}).get(target)
    if scene in {"friend", "group"}:
        return cast(MessageScene, scene), _parse_peer_id(target)

    if extra_scene in {"friend", "group"}:
        return cast(MessageScene, extra_scene), _parse_peer_id(target)

    raise MilkyTargetError(
        f"Milky target requires explicit chat type or cached scene: {target!r}"
    )


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


def split_video_segments(
    segments: list[OutgoingSegment],
) -> tuple[list[OutgoingVideoSegment], list[OutgoingSegment]]:
    """Partition segments into video segments and everything else."""
    videos: list[OutgoingVideoSegment] = []
    others: list[OutgoingSegment] = []
    for segment in segments:
        if isinstance(segment, OutgoingVideoSegment):
            videos.append(segment)
        else:
            others.append(segment)
    return videos, others


def video_segment_to_file_upload(segment: OutgoingVideoSegment) -> OutgoingFileUpload:
    """Convert a video segment into a file upload.

    Used as the fallback when a native video message segment cannot be sent —
    Milky's ``upload_*_file`` API reliably delivers videos as files even when
    the inline video segment path fails.
    """
    return OutgoingFileUpload(
        file_uri=segment.uri,
        file_name=_filename_from_video_uri(segment.uri),
    )


_FILENAME_INVALID_CHARS = re.compile(r'[\x00-\x1f<>:"|?*]')


def _filename_from_video_uri(uri: str) -> str:
    """Derive a safe file name from a video URI, defaulting to ``video.mp4``."""
    parsed = urlparse(uri)
    if parsed.scheme in {"http", "https", "file"}:
        candidate = unquote(parsed.path).rsplit("/", 1)[-1]
    else:
        # base64:// or bare paths — best-effort trailing segment, else default.
        candidate = uri.replace("\\", "/").rsplit("/", 1)[-1]
    candidate = candidate.split("?", 1)[0].split("#", 1)[0].strip()
    candidate = _FILENAME_INVALID_CHARS.sub("_", candidate)
    if not candidate or candidate in {".", ".."} or "." not in candidate:
        return "video.mp4"
    return candidate


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


def _chat_address_from_extra(raw: object) -> ChatAddress | None:
    if raw is None:
        return None
    if isinstance(raw, ChatAddress):
        return raw
    if isinstance(raw, str):
        if not raw:
            return None
        try:
            return ChatAddress.parse(raw)
        except ValueError as exc:
            raise MilkyTargetError(f"Invalid chat_address metadata: {raw!r}") from exc
    if isinstance(raw, dict):
        channel = str(raw.get("channel") or "")
        target_type = str(raw.get("target_type") or raw.get("chat_type") or "")
        target_id = str(raw.get("target_id") or raw.get("chat_id") or "")
        if not (channel and target_type and target_id):
            return None
        try:
            return ChatAddress(
                channel=channel,
                target_type=normalize_target_type(target_type),
                target_id=target_id,
            )
        except ValueError as exc:
            raise MilkyTargetError(f"Invalid chat_address metadata: {raw!r}") from exc
    return None


def _chat_address_from_target(target: str) -> ChatAddress | None:
    if target.count(":") < 2:
        return None
    try:
        address = ChatAddress.parse(target)
    except ValueError as exc:
        raise MilkyTargetError(f"Invalid Milky target address: {target!r}") from exc
    return address if address.is_typed else None


def _scene_and_peer_from_address(address: ChatAddress) -> tuple[MessageScene, int]:
    if address.channel != "milky":
        raise MilkyTargetError(f"Milky cannot send to channel {address.channel!r}")
    if address.target_type == "private":
        return "friend", _parse_peer_id(address.target_id)
    if address.target_type == "group":
        return "group", _parse_peer_id(address.target_id)
    raise MilkyTargetError(
        f"Milky target type must be private or group, got {address.target_type!r}"
    )


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
