"""Milky message segment models and parsers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

from nahida_bot.channels.milky._parsing import (
    as_mapping,
    coerce_str_list,
    field_bool,
    field_int,
    field_str,
)

ImageSubType: TypeAlias = Literal["normal", "sticker"]


@dataclass(slots=True, frozen=True)
class IncomingTextSegment:
    text: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class IncomingMentionSegment:
    user_id: int
    name: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class IncomingMentionAllSegment:
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class IncomingFaceSegment:
    face_id: str
    is_large: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class IncomingReplySegment:
    message_seq: int
    sender_id: int = 0
    sender_name: str = ""
    time: int = 0
    segments: list[IncomingSegment] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class IncomingImageSegment:
    resource_id: str
    temp_url: str = ""
    width: int = 0
    height: int = 0
    summary: str = ""
    sub_type: ImageSubType = "normal"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class IncomingRecordSegment:
    resource_id: str
    temp_url: str = ""
    duration: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class IncomingVideoSegment:
    resource_id: str
    temp_url: str = ""
    width: int = 0
    height: int = 0
    duration: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class IncomingFileSegment:
    file_id: str
    file_name: str
    file_size: int = 0
    file_hash: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class IncomingForwardedMessage:
    """One message inside a resolved Milky merged forward."""

    message_seq: int
    sender_name: str
    avatar_url: str = ""
    time: int = 0
    segments: list[IncomingSegment] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class IncomingForwardSegment:
    """Reference to a merged forward, optionally enriched with its messages."""

    forward_id: str
    title: str = ""
    preview: list[str] = field(default_factory=list)
    summary: str = ""
    messages: list[IncomingForwardedMessage] = field(default_factory=list)
    resolved: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_resolved(self) -> bool:
        """Whether ``get_forwarded_messages`` data has been attached."""
        return self.resolved

    def with_messages(
        self, messages: list[IncomingForwardedMessage]
    ) -> IncomingForwardSegment:
        """Return a copy with resolved forwarded messages attached."""
        return IncomingForwardSegment(
            forward_id=self.forward_id,
            title=self.title,
            preview=list(self.preview),
            summary=self.summary,
            messages=messages,
            resolved=True,
            raw=dict(self.raw),
        )


@dataclass(slots=True, frozen=True)
class IncomingMarketFaceSegment:
    emoji_package_id: int = 0
    emoji_id: str = ""
    key: str = ""
    summary: str = ""
    url: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class IncomingLightAppSegment:
    app_name: str = ""
    json_payload: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class IncomingXmlSegment:
    service_id: int = 0
    xml_payload: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class UnknownIncomingSegment:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


IncomingSegment: TypeAlias = (
    IncomingTextSegment
    | IncomingMentionSegment
    | IncomingMentionAllSegment
    | IncomingFaceSegment
    | IncomingReplySegment
    | IncomingImageSegment
    | IncomingRecordSegment
    | IncomingVideoSegment
    | IncomingFileSegment
    | IncomingForwardSegment
    | IncomingMarketFaceSegment
    | IncomingLightAppSegment
    | IncomingXmlSegment
    | UnknownIncomingSegment
)


@dataclass(slots=True, frozen=True)
class OutgoingTextSegment:
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"type": "text", "data": {"text": self.text}}


@dataclass(slots=True, frozen=True)
class OutgoingMentionSegment:
    user_id: int

    def to_dict(self) -> dict[str, Any]:
        return {"type": "mention", "data": {"user_id": self.user_id}}


@dataclass(slots=True, frozen=True)
class OutgoingMentionAllSegment:
    def to_dict(self) -> dict[str, Any]:
        return {"type": "mention_all", "data": {}}


@dataclass(slots=True, frozen=True)
class OutgoingFaceSegment:
    face_id: str
    is_large: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "face",
            "data": {"face_id": self.face_id, "is_large": self.is_large},
        }


@dataclass(slots=True, frozen=True)
class OutgoingReplySegment:
    message_seq: int

    def to_dict(self) -> dict[str, Any]:
        return {"type": "reply", "data": {"message_seq": self.message_seq}}


@dataclass(slots=True, frozen=True)
class OutgoingImageSegment:
    uri: str
    sub_type: ImageSubType = "normal"
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"uri": self.uri, "sub_type": self.sub_type}
        if self.summary:
            data["summary"] = self.summary
        return {"type": "image", "data": data}


@dataclass(slots=True, frozen=True)
class OutgoingRecordSegment:
    uri: str

    def to_dict(self) -> dict[str, Any]:
        return {"type": "record", "data": {"uri": self.uri}}


@dataclass(slots=True, frozen=True)
class OutgoingVideoSegment:
    uri: str
    thumb_uri: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"uri": self.uri}
        if self.thumb_uri:
            data["thumb_uri"] = self.thumb_uri
        return {"type": "video", "data": data}


@dataclass(slots=True, frozen=True)
class OutgoingForwardedMessage:
    """One message node inside an outgoing merged forward."""

    user_id: int
    sender_name: str
    segments: list[OutgoingSegment]

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "sender_name": self.sender_name,
            "segments": [segment.to_dict() for segment in self.segments],
        }


@dataclass(slots=True, frozen=True)
class OutgoingForwardSegment:
    messages: list[OutgoingForwardedMessage]
    title: str = ""
    preview: list[str] = field(default_factory=list)
    summary: str = ""
    prompt: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "messages": [message.to_dict() for message in self.messages]
        }
        if self.title:
            data["title"] = self.title
        if self.preview:
            data["preview"] = list(self.preview)
        if self.summary:
            data["summary"] = self.summary
        if self.prompt:
            data["prompt"] = self.prompt
        return {"type": "forward", "data": data}


@dataclass(slots=True, frozen=True)
class OutgoingLightAppSegment:
    json_payload: str

    def to_dict(self) -> dict[str, Any]:
        return {"type": "light_app", "data": {"json_payload": self.json_payload}}


OutgoingSegment: TypeAlias = (
    OutgoingTextSegment
    | OutgoingMentionSegment
    | OutgoingMentionAllSegment
    | OutgoingFaceSegment
    | OutgoingReplySegment
    | OutgoingImageSegment
    | OutgoingRecordSegment
    | OutgoingVideoSegment
    | OutgoingForwardSegment
    | OutgoingLightAppSegment
)


@dataclass(slots=True, frozen=True)
class OutgoingFileUpload:
    """Milky file upload payload.

    Files are sent through the Milky file APIs, not as ``OutgoingSegment``
    values in ``send_private_message`` or ``send_group_message``.
    """

    file_uri: str
    file_name: str
    parent_folder_id: str = "/"

    def private_payload(self, user_id: int) -> dict[str, Any]:
        return {
            "user_id": user_id,
            "file_uri": self.file_uri,
            "file_name": self.file_name,
        }

    def group_payload(self, group_id: int) -> dict[str, Any]:
        return {
            "group_id": group_id,
            "parent_folder_id": self.parent_folder_id,
            "file_uri": self.file_uri,
            "file_name": self.file_name,
        }


def parse_incoming_segments(raw_segments: object) -> list[IncomingSegment]:
    """Parse a list of Milky incoming segment dictionaries."""
    if not isinstance(raw_segments, list):
        return []
    return [
        parse_incoming_segment(raw) for raw in raw_segments if isinstance(raw, dict)
    ]


def parse_incoming_segment(raw: dict[str, Any]) -> IncomingSegment:
    """Parse one Milky incoming segment dictionary."""
    segment_type = field_str(raw, "type")
    data = as_mapping(raw.get("data"))

    if segment_type == "text":
        return IncomingTextSegment(text=field_str(data, "text"), raw=raw)
    if segment_type == "mention":
        return IncomingMentionSegment(
            user_id=field_int(data, "user_id"),
            name=field_str(data, "name"),
            raw=raw,
        )
    if segment_type == "mention_all":
        return IncomingMentionAllSegment(raw=raw)
    if segment_type == "face":
        return IncomingFaceSegment(
            face_id=field_str(data, "face_id"),
            is_large=field_bool(data, "is_large"),
            raw=raw,
        )
    if segment_type == "reply":
        return IncomingReplySegment(
            message_seq=field_int(data, "message_seq"),
            sender_id=field_int(data, "sender_id"),
            sender_name=field_str(data, "sender_name"),
            time=field_int(data, "time"),
            segments=parse_incoming_segments(data.get("segments")),
            raw=raw,
        )
    if segment_type == "image":
        return IncomingImageSegment(
            resource_id=field_str(data, "resource_id"),
            temp_url=field_str(data, "temp_url"),
            width=field_int(data, "width"),
            height=field_int(data, "height"),
            summary=field_str(data, "summary"),
            sub_type=_image_sub_type(data.get("sub_type")),
            raw=raw,
        )
    if segment_type == "record":
        return IncomingRecordSegment(
            resource_id=field_str(data, "resource_id"),
            temp_url=field_str(data, "temp_url"),
            duration=field_int(data, "duration"),
            raw=raw,
        )
    if segment_type == "video":
        return IncomingVideoSegment(
            resource_id=field_str(data, "resource_id"),
            temp_url=field_str(data, "temp_url"),
            width=field_int(data, "width"),
            height=field_int(data, "height"),
            duration=field_int(data, "duration"),
            raw=raw,
        )
    if segment_type == "file":
        return IncomingFileSegment(
            file_id=field_str(data, "file_id"),
            file_name=field_str(data, "file_name"),
            file_size=field_int(data, "file_size"),
            file_hash=field_str(data, "file_hash"),
            raw=raw,
        )
    if segment_type == "forward":
        return IncomingForwardSegment(
            forward_id=field_str(data, "forward_id"),
            title=field_str(data, "title"),
            preview=coerce_str_list(data.get("preview")),
            summary=field_str(data, "summary"),
            raw=raw,
        )
    if segment_type == "market_face":
        return IncomingMarketFaceSegment(
            emoji_package_id=field_int(data, "emoji_package_id"),
            emoji_id=field_str(data, "emoji_id"),
            key=field_str(data, "key"),
            summary=field_str(data, "summary"),
            url=field_str(data, "url"),
            raw=raw,
        )
    if segment_type == "light_app":
        return IncomingLightAppSegment(
            app_name=field_str(data, "app_name"),
            json_payload=field_str(data, "json_payload"),
            raw=raw,
        )
    if segment_type == "xml":
        return IncomingXmlSegment(
            service_id=field_int(data, "service_id"),
            xml_payload=field_str(data, "xml_payload"),
            raw=raw,
        )
    return UnknownIncomingSegment(type=segment_type, data=data, raw=raw)


def parse_incoming_forwarded_messages(
    raw_messages: object,
) -> list[IncomingForwardedMessage]:
    """Parse ``get_forwarded_messages`` output into forwarded message models."""
    if not isinstance(raw_messages, list):
        return []
    return [
        parse_incoming_forwarded_message(raw)
        for raw in raw_messages
        if isinstance(raw, dict)
    ]


def parse_incoming_forwarded_message(
    raw: dict[str, Any],
) -> IncomingForwardedMessage:
    """Parse one ``IncomingForwardedMessage`` dictionary."""
    return IncomingForwardedMessage(
        message_seq=field_int(raw, "message_seq"),
        sender_name=field_str(raw, "sender_name"),
        avatar_url=field_str(raw, "avatar_url"),
        time=field_int(raw, "time"),
        segments=parse_incoming_segments(raw.get("segments")),
        raw=raw,
    )


def outgoing_segments_to_dicts(
    segments: list[OutgoingSegment],
) -> list[dict[str, Any]]:
    """Serialize outgoing segments for Milky API payloads."""
    return [segment.to_dict() for segment in segments]


def render_segments_plain_text(
    segments: list[IncomingSegment],
    *,
    max_forward_depth: int = 3,
    _depth: int = 0,
) -> str:
    """Render incoming segments into text suitable for Agent context."""
    return "".join(
        render_segment_plain_text(
            segment, max_forward_depth=max_forward_depth, _depth=_depth
        )
        for segment in segments
    )


def render_segment_plain_text(
    segment: IncomingSegment,
    *,
    max_forward_depth: int = 3,
    _depth: int = 0,
) -> str:
    """Render one incoming segment into a compact readable representation."""
    if isinstance(segment, IncomingTextSegment):
        return segment.text
    if isinstance(segment, IncomingMentionSegment):
        return f"@{segment.name or segment.user_id}"
    if isinstance(segment, IncomingMentionAllSegment):
        return "@all"
    if isinstance(segment, IncomingFaceSegment):
        suffix = ", large=true" if segment.is_large else ""
        return f"[Face: id={segment.face_id}{suffix}]"
    if isinstance(segment, IncomingReplySegment):
        quoted = ""
        if segment.segments:
            quoted = render_segments_plain_text(
                segment.segments,
                max_forward_depth=max_forward_depth,
                _depth=_depth,
            )
            quoted = f", content={quoted}"
        return f"[Reply: message_seq={segment.message_seq}{quoted}]"
    if isinstance(segment, IncomingImageSegment):
        size = f", size={segment.width}x{segment.height}" if segment.width else ""
        url = f", temp_url={segment.temp_url}" if segment.temp_url else ""
        return (
            f"[Media: type=image, resource_id={segment.resource_id}"
            f"{size}{url}, summary={segment.summary}]"
        )
    if isinstance(segment, IncomingRecordSegment):
        url = f", temp_url={segment.temp_url}" if segment.temp_url else ""
        return (
            f"[Media: type=record, resource_id={segment.resource_id}, "
            f"duration={segment.duration}s{url}]"
        )
    if isinstance(segment, IncomingVideoSegment):
        size = f", size={segment.width}x{segment.height}" if segment.width else ""
        url = f", temp_url={segment.temp_url}" if segment.temp_url else ""
        return (
            f"[Media: type=video, resource_id={segment.resource_id}"
            f"{size}, duration={segment.duration}s{url}]"
        )
    if isinstance(segment, IncomingFileSegment):
        return (
            f"[File: name={segment.file_name}, file_id={segment.file_id}, "
            f"size={segment.file_size}]"
        )
    if isinstance(segment, IncomingForwardSegment):
        return _render_forward_segment(
            segment,
            max_forward_depth=max_forward_depth,
            depth=_depth,
        )
    if isinstance(segment, IncomingMarketFaceSegment):
        return f"[MarketFace: summary={segment.summary}, url={segment.url}]"
    if isinstance(segment, IncomingLightAppSegment):
        return f"[LightApp: app_name={segment.app_name}]"
    if isinstance(segment, IncomingXmlSegment):
        return f"[XML: service_id={segment.service_id}]"
    return f"[UnsupportedSegment: type={segment.type}]"


def _render_forward_segment(
    segment: IncomingForwardSegment,
    *,
    max_forward_depth: int,
    depth: int,
) -> str:
    preview = "; ".join(segment.preview)
    if not segment.messages:
        return (
            f"[Forward: id={segment.forward_id}, title={segment.title}, "
            f"summary={segment.summary}, preview={preview}]"
        )
    if depth >= max_forward_depth:
        return (
            f"[Forward: id={segment.forward_id}, title={segment.title}, "
            f"messages={len(segment.messages)}, truncated=true]"
        )

    lines = [
        (
            f"[Forward: id={segment.forward_id}, title={segment.title}, "
            f"summary={segment.summary}]"
        )
    ]
    for message in segment.messages:
        content = "".join(
            render_segment_plain_text(
                child,
                max_forward_depth=max_forward_depth,
                _depth=depth + 1,
            )
            for child in message.segments
        )
        lines.append(f"- {message.sender_name}: {content}")
    return "\n".join(lines)


def _image_sub_type(value: object) -> ImageSubType:
    return "sticker" if value == "sticker" else "normal"
