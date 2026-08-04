"""Milky incoming message conversion."""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import datetime
from typing import Any, Protocol

from nahida_bot.channels.milky._parsing import coerce_int, coerce_str
from nahida_bot.channels.milky.config import MilkyPluginConfig
from nahida_bot.channels.milky.segments import (
    IncomingFileSegment,
    IncomingForwardSegment,
    IncomingForwardedMessage,
    IncomingImageSegment,
    IncomingMentionSegment,
    IncomingRecordSegment,
    IncomingReplySegment,
    IncomingSegment,
    IncomingVideoSegment,
    is_file_only_segments,
    parse_incoming_segments,
    render_segments_plain_text,
)
from nahida_bot.core.message_context import (
    chat_context_from_values,
    context_from_inbound,
    sender_context_from_values,
)
from nahida_bot.plugins.base import InboundAttachment, InboundMessage

_MILKY_TIMESTAMP_CORRECTION_TOLERANCE_SECONDS = 300


class ForwardMessageClient(Protocol):
    """Small client surface needed for resolving merged forwards."""

    async def get_forwarded_messages(
        self, forward_id: str
    ) -> list[IncomingForwardedMessage]: ...


class WarningLogger(Protocol):
    """Logger callable compatible with structlog warning methods."""

    def __call__(self, event: str, **kwargs: object) -> object: ...


class MilkyMessageConverter:
    """Convert Milky ``message_receive`` data into ``InboundMessage``."""

    def __init__(
        self,
        config: MilkyPluginConfig,
        *,
        self_id: int = 0,
        forward_client: ForwardMessageClient | None = None,
        logger_warning: WarningLogger | None = None,
        observe_untriggered_group_messages: bool = False,
    ) -> None:
        self._config = config
        self._self_id = self_id
        self._forward_client = forward_client
        self._logger_warning = logger_warning
        self._observe_untriggered_group_messages = observe_untriggered_group_messages

    async def to_inbound(
        self, message_data: dict[str, Any], *, raw_event: dict[str, Any] | None = None
    ) -> InboundMessage | None:
        """Convert one Milky incoming message, returning None if filtered."""
        scene = coerce_str(message_data.get("message_scene"))
        peer_id = coerce_str(message_data.get("peer_id"))
        sender_id = coerce_str(message_data.get("sender_id"))
        is_group = scene == "group"

        if not self._is_allowed(scene, peer_id):
            return None

        segments = parse_incoming_segments(message_data.get("segments"))
        if self._forward_client is not None and self._config.max_forward_depth > 0:
            segments = await self._resolve_forward_segments(segments, depth=0)

        if (
            is_group
            and not self._observe_untriggered_group_messages
            and not self._should_accept_group_message(segments)
            and not is_file_only_segments(segments)
        ):
            return None

        visible_segments = (
            self._strip_self_mentions(segments) if is_group else list(segments)
        )
        text = render_segments_plain_text(
            visible_segments,
            max_forward_depth=self._config.max_forward_depth,
        ).strip()
        if len(text) > self._config.forward_render_max_chars:
            text = text[: self._config.forward_render_max_chars] + "\n[Truncated]"

        if not text:
            return None

        attachments = self._extract_attachments(segments)
        # Enrich file attachments with Milky scene/peer context so that
        # downstream URL resolution knows whether to call the group or
        # private file-download API.
        for i, att in enumerate(attachments):
            if att.kind == "file" and not att.url and scene and peer_id:
                attachments[i] = replace(
                    att,
                    metadata={
                        **att.metadata,
                        "_milky_scene": scene,
                        "_milky_peer_id": peer_id,
                    },
                )
        sender_context = sender_context_from_values(
            display_name=self._sender_display_name(message_data),
            platform_user_id=sender_id or "0",
            role_tags=self._sender_role_tags(message_data),
            is_self=self._self_id > 0 and sender_id == str(self._self_id),
        )
        chat_context = chat_context_from_values(
            platform="milky",
            chat_type="group" if is_group else "private",
            platform_chat_id=peer_id,
            display_name=self._chat_display_name(message_data, is_group),
        )

        inbound = InboundMessage(
            message_id=coerce_str(message_data.get("message_seq"), "0"),
            platform="milky",
            chat_id=peer_id,
            user_id=sender_id or "0",
            text=text,
            raw_event=raw_event or message_data,
            is_group=is_group,
            reply_to=self._reply_to(segments),
            timestamp=_normalize_milky_timestamp(message_data.get("time")),
            command_prefix=self._config.command_prefix,
            attachments=attachments,
            sender_context=sender_context,
            chat_context=chat_context,
            mentions_bot=self._has_self_mention(segments),
            mentioned_user_ids=self._mentioned_user_ids(segments),
        )
        return replace(inbound, message_context=context_from_inbound(inbound))

    async def _resolve_forward_segments(
        self, segments: list[IncomingSegment], *, depth: int
    ) -> list[IncomingSegment]:
        resolved: list[IncomingSegment] = []
        for segment in segments:
            if (
                isinstance(segment, IncomingForwardSegment)
                and segment.forward_id
                and depth < self._config.max_forward_depth
                and self._forward_client is not None
            ):
                try:
                    messages = await self._forward_client.get_forwarded_messages(
                        segment.forward_id
                    )
                    messages = messages[: self._config.max_forward_messages]
                    messages = [
                        IncomingForwardedMessage(
                            message_seq=message.message_seq,
                            sender_name=message.sender_name,
                            avatar_url=message.avatar_url,
                            time=message.time,
                            segments=await self._resolve_forward_segments(
                                message.segments, depth=depth + 1
                            ),
                            raw=message.raw,
                        )
                        for message in messages
                    ]
                    resolved.append(segment.with_messages(messages))
                except Exception as exc:  # noqa: BLE001
                    if self._logger_warning is not None:
                        self._logger_warning(
                            "milky.forward_resolve_failed",
                            forward_id=segment.forward_id,
                            error=str(exc),
                        )
                    resolved.append(segment)
            else:
                resolved.append(segment)
        return resolved

    def _is_allowed(self, scene: str, peer_id: str) -> bool:
        if scene == "friend" and self._config.allowed_friends:
            return peer_id in self._config.allowed_friends
        if scene == "group" and self._config.allowed_groups:
            return peer_id in self._config.allowed_groups
        return True

    def _should_accept_group_message(self, segments: list[IncomingSegment]) -> bool:
        if self._config.group_trigger_mode == "always":
            return True
        if self._config.group_trigger_mode == "none":
            return False
        has_mention = self._has_self_mention(segments)
        text = render_segments_plain_text(
            segments, max_forward_depth=self._config.max_forward_depth
        ).lstrip()
        if self._config.group_trigger_mode == "command":
            return has_mention or text.startswith(self._config.command_prefix)
        return has_mention

    def _has_self_mention(self, segments: list[IncomingSegment]) -> bool:
        if self._self_id <= 0:
            return False
        return any(
            isinstance(segment, IncomingMentionSegment)
            and segment.user_id == self._self_id
            for segment in segments
        )

    @staticmethod
    def _mentioned_user_ids(segments: list[IncomingSegment]) -> tuple[str, ...]:
        ids: list[str] = []
        for segment in segments:
            if isinstance(segment, IncomingMentionSegment):
                ids.append(str(segment.user_id))
        return tuple(dict.fromkeys(ids))

    def _strip_self_mentions(
        self, segments: list[IncomingSegment]
    ) -> list[IncomingSegment]:
        if self._self_id <= 0:
            return list(segments)
        return [
            segment
            for segment in segments
            if not (
                isinstance(segment, IncomingMentionSegment)
                and segment.user_id == self._self_id
            )
        ]

    @staticmethod
    def _chat_display_name(message_data: dict[str, Any], is_group: bool) -> str:
        """Extract the display name for a chat from the message data.

        Lagrange.Milky nests group metadata inside a ``group`` object
        rather than at the top level, so we check both locations.
        """
        display_name = coerce_str(
            message_data.get("group_name")
            or message_data.get("peer_name")
            or message_data.get("friend_name")
        )
        if not display_name and is_group:
            group_obj = message_data.get("group")
            if isinstance(group_obj, dict):
                display_name = coerce_str(group_obj.get("group_name"))
        return display_name

    @staticmethod
    def _sender_display_name(message_data: dict[str, Any]) -> str:
        # Group card (群名片) vs QQ nickname: when they differ in a group
        # chat, show both as "GroupCard(QQNick)" so the LLM can tell them
        # apart and address the user correctly.
        card = coerce_str(message_data.get("card") or message_data.get("member_name"))
        nickname = coerce_str(
            message_data.get("sender_name")
            or message_data.get("sender_nickname")
            or message_data.get("nickname")
        )

        # Lagrange.Milky puts sender info in nested objects rather than
        # top-level fields.  Check group_member (group chats) and friend
        # (private chats) when the flat fields are empty.
        if not card:
            sender = message_data.get("sender")
            if isinstance(sender, dict):
                card = coerce_str(sender.get("card"))
            if not card:
                gm = message_data.get("group_member")
                if isinstance(gm, dict):
                    card = coerce_str(gm.get("card"))

        if not nickname:
            sender = message_data.get("sender")
            if isinstance(sender, dict):
                nickname = coerce_str(sender.get("name") or sender.get("nickname"))
            if not nickname:
                gm = message_data.get("group_member")
                if isinstance(gm, dict):
                    nickname = coerce_str(gm.get("nickname"))
            if not nickname:
                friend = message_data.get("friend")
                if isinstance(friend, dict):
                    nickname = coerce_str(friend.get("nickname"))

        # TODO(milky-sender-fallback): When all sources above yield empty,
        # consider calling the Milky API (e.g. get_group_member_info /
        # get_stranger_info) to resolve the display name from sender_id.
        # This would add latency per message and needs careful caching
        # (e.g. a small TTL-based LRU keyed by group_id+sender_id for
        # group chats, or sender_id for private chats) to avoid hitting
        # rate limits on high-traffic groups.  The cache should live on
        # the MilkyMessageConverter or MilkyClient instance so it is
        # shared across messages and cleared on reconnect.

        if card and nickname and card != nickname:
            return f"{card}({nickname})"
        return card or nickname

    @staticmethod
    def _sender_role_tags(message_data: dict[str, Any]) -> tuple[str, ...]:
        tags: list[str] = []
        role = coerce_str(
            message_data.get("sender_role")
            or message_data.get("member_role")
            or message_data.get("role")
        ).lower()
        if role in {"owner", "admin", "administrator"}:
            tags.append("owner" if role == "owner" else "admin")
        if message_data.get("is_owner") is True:
            tags.append("owner")
        if message_data.get("is_admin") is True:
            tags.append("admin")

        sender = message_data.get("sender")
        if isinstance(sender, dict):
            nested_role = coerce_str(
                sender.get("role") or sender.get("member_role")
            ).lower()
            if nested_role in {"owner", "admin", "administrator"}:
                tags.append("owner" if nested_role == "owner" else "admin")
        # Lagrange.Milky puts role info in the ``group_member`` nested object.
        if not tags:
            gm = message_data.get("group_member")
            if isinstance(gm, dict):
                gm_role = coerce_str(gm.get("role")).lower()
                if gm_role in {"owner", "admin", "administrator"}:
                    tags.append("owner" if gm_role == "owner" else "admin")
        return tuple(dict.fromkeys(tags))

    @staticmethod
    def _reply_to(segments: list[IncomingSegment]) -> str:
        for segment in segments:
            if isinstance(segment, IncomingReplySegment) and segment.message_seq:
                return str(segment.message_seq)
        return ""

    @staticmethod
    def _extract_attachments(
        segments: list[IncomingSegment],
    ) -> list[InboundAttachment]:
        """Extract first-class InboundAttachment objects from media segments."""
        attachments: list[InboundAttachment] = []
        for segment in segments:
            if isinstance(segment, IncomingImageSegment):
                attachments.append(
                    InboundAttachment(
                        kind="image",
                        platform_id=segment.resource_id,
                        url=segment.temp_url,
                        width=segment.width,
                        height=segment.height,
                        alt_text=segment.summary,
                        metadata={
                            "sub_type": segment.sub_type,
                            "trusted_url": bool(segment.temp_url),
                        },
                    )
                )
            elif isinstance(segment, IncomingRecordSegment):
                attachments.append(
                    InboundAttachment(
                        kind="audio",
                        platform_id=segment.resource_id,
                        url=segment.temp_url,
                        metadata={"duration": segment.duration},
                    )
                )
            elif isinstance(segment, IncomingVideoSegment):
                attachments.append(
                    InboundAttachment(
                        kind="video",
                        platform_id=segment.resource_id,
                        url=segment.temp_url,
                        width=segment.width,
                        height=segment.height,
                        metadata={"duration": segment.duration},
                    )
                )
            elif isinstance(segment, IncomingFileSegment):
                attachments.append(
                    InboundAttachment(
                        kind="file",
                        platform_id=segment.file_id,
                        file_size=segment.file_size,
                        metadata={
                            "file_name": segment.file_name,
                            "file_size": segment.file_size,
                            "file_hash": segment.file_hash,
                        },
                    )
                )
        return attachments


def _normalize_milky_timestamp(
    value: object,
    *,
    now: float | None = None,
    local_utc_offset_seconds: float | None = None,
) -> float:
    """Normalize Milky message time to a real Unix timestamp.

    Some Milky/Lagrange deployments emit a timestamp that has already been
    shifted by the local UTC offset. For example, UTC+8 deployments can produce
    ``real_epoch - 28800``. Detect that shape near receive time and repair it at
    the channel boundary so the rest of the bot only sees standard epoch seconds.
    """
    raw = float(coerce_int(value))
    if raw <= 0:
        return raw

    observed_now = time.time() if now is None else now
    offset = (
        _local_utc_offset_seconds(observed_now)
        if local_utc_offset_seconds is None
        else local_utc_offset_seconds
    )
    if not offset:
        return raw

    corrected = raw + offset
    tolerance = _MILKY_TIMESTAMP_CORRECTION_TOLERANCE_SECONDS
    if (
        abs((observed_now - raw) - offset) <= tolerance
        and abs(observed_now - corrected) <= tolerance
    ):
        return corrected
    return raw


def _local_utc_offset_seconds(timestamp: float) -> float:
    offset = datetime.fromtimestamp(timestamp).astimezone().utcoffset()
    return offset.total_seconds() if offset is not None else 0.0
