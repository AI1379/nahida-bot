"""Normalized message types for nahida-bot plugin communication."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, frozen=True)
class InboundAttachment:
    """First-class inbound media object from an external platform."""

    kind: str  # image, audio, video, file
    platform_id: str  # resource_id / file_id
    url: str = ""  # temp URL, may expire
    path: str = ""  # local cached path, if downloaded
    mime_type: str = ""
    file_size: int = 0
    width: int = 0
    height: int = 0
    alt_text: str = ""  # platform summary or generated description
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class SenderContext:
    """Short, provider-safe facts about the sender of one inbound message."""

    display_name: str = ""
    platform_user_id: str = ""
    role_tags: tuple[str, ...] = ()
    is_bot: bool = False
    is_self: bool = False


@dataclass(slots=True, frozen=True)
class ChatContext:
    """Short, provider-safe facts about the chat that produced one message."""

    platform: str = ""
    chat_type: str = "unknown"  # private, group, channel, thread, unknown
    platform_chat_id: str = ""
    display_name: str = ""


@dataclass(slots=True, frozen=True)
class MessageContext:
    """Per-turn facts rendered as structured LLM-visible context data."""

    timestamp: float = 0.0
    channel: str = ""
    chat_type: str = "unknown"
    chat_id: str = ""
    chat_display_name: str = ""
    sender_id: str = ""
    sender_display_name: str = ""
    sender_role_tags: tuple[str, ...] = ()
    extra_tags: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class InboundMessage:
    """Normalized message received from an external platform."""

    message_id: str
    platform: str  # e.g. "telegram", "qq"
    chat_id: str
    user_id: str
    text: str
    raw_event: dict[str, Any]
    is_group: bool = False
    reply_to: str = ""
    timestamp: float = 0.0
    command_prefix: str = "/"
    attachments: list[InboundAttachment] = field(default_factory=list)
    sender_context: SenderContext | None = None
    chat_context: ChatContext | None = None
    message_context: MessageContext | None = None
    mentions_bot: bool = False
    mentioned_user_ids: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class MediaDownloadResult:
    """Result of downloading a media file from a platform."""

    path: str  # local file path where the file was saved
    file_name: str = ""
    mime_type: str = ""
    file_size: int = 0


@dataclass(slots=True, frozen=True)
class Attachment:
    """A file attachment for an outbound message."""

    type: str  # "photo", "document", "audio", "video"
    path: str  # local file path
    filename: str = ""
    mime_type: str = ""
    caption: str = ""


@dataclass(slots=True, frozen=True)
class OutboundMessage:
    """Normalized message to send to an external platform."""

    text: str
    reply_to: str = ""
    reasoning: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    attachments: list[Attachment] = field(default_factory=list)
