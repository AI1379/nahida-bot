"""Normalize Milky file-upload events into inbound messages."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from nahida_bot.channels.milky._parsing import as_mapping, coerce_int, coerce_str
from nahida_bot.core.message_context import (
    chat_context_from_values,
    context_from_inbound,
    sender_context_from_values,
)
from nahida_bot.plugins.base import InboundAttachment, InboundMessage


def _first_truthy(*values: object) -> object | None:
    """Return the first truthy event-field candidate."""
    return next((value for value in values if value), None)


def _chat_fields(
    event_type: str,
    data: dict[str, Any],
    group: dict[str, Any],
    user: dict[str, Any],
) -> tuple[bool, str, str, str]:
    is_group = event_type == "group_file_upload"
    group_id = coerce_str(
        _first_truthy(
            data.get("group_id"),
            data.get("peer_id"),
            group.get("group_id"),
            group.get("id"),
        )
    )
    user_id = coerce_str(
        _first_truthy(
            data.get("user_id"),
            data.get("sender_id"),
            user.get("user_id"),
            user.get("id"),
        )
    )
    return is_group, group_id, user_id, group_id if is_group else user_id


def _file_fields(
    data: dict[str, Any],
    file_data: dict[str, Any],
) -> tuple[str, str, int, str, str]:
    return (
        coerce_str(
            _first_truthy(
                data.get("file_id"),
                file_data.get("file_id"),
                file_data.get("id"),
            )
        ),
        coerce_str(
            _first_truthy(
                data.get("file_name"),
                data.get("name"),
                file_data.get("file_name"),
                file_data.get("name"),
            )
        ),
        coerce_int(
            _first_truthy(
                data.get("file_size"),
                data.get("size"),
                file_data.get("file_size"),
                file_data.get("size"),
            )
        ),
        coerce_str(
            _first_truthy(
                data.get("file_hash"),
                data.get("hash"),
                file_data.get("file_hash"),
                file_data.get("hash"),
            )
        ),
        coerce_str(
            _first_truthy(
                data.get("download_url"),
                data.get("url"),
                file_data.get("download_url"),
                file_data.get("url"),
            )
        ),
    )


def _display_fields(
    data: dict[str, Any],
    group: dict[str, Any],
    user: dict[str, Any],
) -> tuple[str, str]:
    sender_name = coerce_str(
        _first_truthy(
            data.get("sender_name"),
            data.get("nickname"),
            user.get("nickname"),
            user.get("name"),
        )
    )
    chat_name = coerce_str(
        _first_truthy(
            data.get("group_name"),
            data.get("peer_name"),
            data.get("friend_name"),
            group.get("group_name"),
            group.get("name"),
            user.get("nickname"),
            user.get("name"),
        )
    )
    return sender_name, chat_name


@dataclass(slots=True, frozen=True)
class MilkyFileUpload:
    """Canonical fields shared by friend and group file-upload events."""

    event_type: str
    is_group: bool
    group_id: str
    user_id: str
    chat_id: str
    file_id: str
    file_name: str
    file_size: int
    file_hash: str
    download_url: str
    sender_name: str
    chat_name: str
    message_id: str
    timestamp: float

    @classmethod
    def from_event(
        cls,
        event_type: str,
        data: dict[str, Any],
    ) -> MilkyFileUpload | None:
        """Parse the known Milky field aliases into one payload."""
        group = as_mapping(data.get("group"))
        user = as_mapping(data.get("user"))
        file_data = as_mapping(data.get("file"))
        is_group, group_id, user_id, chat_id = _chat_fields(
            event_type,
            data,
            group,
            user,
        )
        if not chat_id:
            return None

        file_id, file_name, file_size, file_hash, download_url = _file_fields(
            data,
            file_data,
        )
        if not file_id and not file_name:
            return None
        sender_name, chat_name = _display_fields(data, group, user)
        message_id = coerce_str(
            _first_truthy(
                data.get("message_seq"),
                data.get("event_id"),
                data.get("time"),
                file_id,
                file_name,
            )
        )
        return cls(
            event_type=event_type,
            is_group=is_group,
            group_id=group_id,
            user_id=user_id,
            chat_id=chat_id,
            file_id=file_id,
            file_name=file_name,
            file_size=file_size,
            file_hash=file_hash,
            download_url=download_url,
            sender_name=sender_name,
            chat_name=chat_name,
            message_id=message_id,
            timestamp=float(coerce_int(data.get("time"))),
        )

    def to_inbound(
        self,
        *,
        raw_event: dict[str, Any],
        command_prefix: str,
        self_id: int,
    ) -> InboundMessage:
        """Build the normalized SDK message and its canonical message context."""
        attachment = InboundAttachment(
            kind="file",
            platform_id=self.file_id,
            url=self.download_url,
            file_size=self.file_size,
            metadata=self._attachment_metadata(),
        )
        inbound = InboundMessage(
            message_id=self.message_id,
            platform="milky",
            chat_id=self.chat_id,
            user_id=self.user_id or "0",
            text=render_file_upload_text(
                file_name=self.file_name,
                file_id=self.file_id,
                file_size=self.file_size,
            ),
            raw_event=raw_event,
            is_group=self.is_group,
            timestamp=self.timestamp,
            command_prefix=command_prefix,
            attachments=[attachment],
            sender_context=sender_context_from_values(
                display_name=self.sender_name,
                platform_user_id=self.user_id or "0",
                is_self=self_id > 0 and self.user_id == str(self_id),
            ),
            chat_context=chat_context_from_values(
                platform="milky",
                chat_type="group" if self.is_group else "private",
                platform_chat_id=self.chat_id,
                display_name=self.chat_name,
            ),
            mentions_bot=False,
            mentioned_user_ids=(),
        )
        return replace(inbound, message_context=context_from_inbound(inbound))

    def _attachment_metadata(self) -> dict[str, object]:
        metadata: dict[str, object] = {
            "file_name": self.file_name,
            "file_size": self.file_size,
            "file_hash": self.file_hash,
            "milky_event_type": self.event_type,
        }
        if self.is_group:
            metadata["group_id"] = self.group_id
        else:
            metadata["user_id"] = self.user_id
        return metadata


def render_file_upload_text(
    *,
    file_name: str,
    file_id: str,
    file_size: int,
) -> str:
    """Render the stable text marker used for uploaded files."""
    name = file_name or "<unknown>"
    return f"[File: name={name}, file_id={file_id}, size={file_size}]"
