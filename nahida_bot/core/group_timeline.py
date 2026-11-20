"""Project one group-chat snapshot into a chronological message context."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from nahida_bot.agent.memory.models import ConversationTurn, MemoryRecord
from nahida_bot.core.message_context import (
    context_from_inbound,
    message_context_from_metadata,
    message_context_to_metadata,
    render_message_with_context,
)
from nahida_bot_sdk.chat_address import ChatAddress
from nahida_bot_sdk.messaging import (
    AttentionFrame,
    InboundAttachment,
    InboundMessage,
    MessageContext,
    OutboundMessage,
)

logger = structlog.get_logger(__name__)

GROUP_TIMELINE_INSTRUCTION = (
    "## Group Chat Timeline\n"
    "The group timeline is ordered from oldest to newest. Its bot_message "
    "entries are your prior group replies. Use message_id and reply_to_message_id "
    "to follow references, including quoted messages from outside the recent window. "
    "Only entries marked current_request assign work. Use mentions_bot only "
    "on those entries to decide if you were directly addressed. A focus_message is a "
    "group message selected for a possible contribution. Other entries are "
    "background, including any that arrived after the current request."
)
_MAX_QUOTE_DEPTH = 3
_MAX_QUOTE_TARGETS = 8


@dataclass(slots=True, frozen=True)
class GroupRunInput:
    messages: tuple[InboundMessage, ...]
    attention: AttentionFrame | None = None


@dataclass(slots=True, frozen=True)
class TimelineLine:
    text: str
    message_id: str
    role: str
    attachments: tuple[InboundAttachment, ...] = ()
    quote_depth: int = 0
    media_metadata: dict[str, Any] | None = None


@dataclass(slots=True, frozen=True)
class GroupTimeline:
    heading: str
    lines: tuple[TimelineLine, ...]
    snapshot_at: datetime

    @property
    def background_ids(self) -> frozenset[str]:
        return frozenset(
            line.message_id
            for line in self.lines
            if line.message_id and line.role in {"group_message", "focus_message"}
        )


@dataclass(slots=True, frozen=True)
class TimelineWindow:
    snapshot_at: datetime
    reset_at: datetime | None
    max_messages: int
    max_chars: int
    ttl_seconds: int
    topic_gap_seconds: int
    capture_observations: bool


@dataclass(slots=True, frozen=True)
class _Entry:
    time: datetime
    context: MessageContext
    text: str
    role: str
    attachments: tuple[InboundAttachment, ...] = ()
    observed_only: bool = False
    media_metadata: dict[str, Any] | None = None

    @property
    def is_input(self) -> bool:
        return self.role in {"current_request", "focus_message"}

    def render(self, max_chars: int | None = None) -> TimelineLine:
        return TimelineLine(
            render_message_with_context(
                self.text, self.context, role=self.role, max_chars=max_chars
            ),
            self.context.message_id,
            self.role,
            self.attachments,
        )


def acknowledged_group_turn(
    address: ChatAddress, message_id: str, outbound: OutboundMessage, sent_at: datetime
) -> ConversationTurn:
    context = MessageContext(
        timestamp=sent_at.timestamp(),
        channel=address.channel,
        chat_type="group",
        chat_id=address.target_id,
        sender_display_name="bot",
        message_id=message_id,
        reply_to_message_id=outbound.reply_to,
    )
    return ConversationTurn(
        role="assistant",
        content=outbound.text,
        source="group_delivery",
        metadata={
            "message_id": message_id,
            "reply_to": outbound.reply_to,
            "message_context": message_context_to_metadata(context),
            "attachments": [
                {
                    "kind": "image" if attachment.type == "photo" else attachment.type,
                    "description": attachment.caption,
                }
                for attachment in outbound.attachments
            ],
        },
        created_at=sent_at,
    )


def _referenced_ids(run: GroupRunInput) -> tuple[str, ...]:
    ids = dict.fromkeys(
        context.reply_to_message_id
        for message in run.messages
        if (context := context_from_inbound(message)).reply_to_message_id
    )
    if run.attention and run.attention.reply_to_message_id:
        ids.setdefault(run.attention.reply_to_message_id)
    return tuple(ids)


async def build_group_timeline(
    records: list[MemoryRecord],
    run: GroupRunInput,
    window: TimelineWindow,
    *,
    find_turn: Callable[[str], Awaitable[MemoryRecord | None]] | None = None,
) -> GroupTimeline:
    address = context_from_inbound(run.messages[0])
    entries = [
        entry
        for record in records
        if (entry := _from_record(record, address, window)) is not None
    ]
    attention_messages = run.attention.messages if run.attention else ()
    for message in attention_messages:
        context = context_from_inbound(message)
        if _same_group(context, address):
            entry = _from_inbound(message, context, window, "group_message")
            if window.reset_at is None or entry.time >= window.reset_at:
                entries.append(entry)

    input_role = "focus_message" if run.attention else "current_request"
    recorded_times = {
        entry.context.message_id: entry.context.timestamp
        for entry in reversed(entries)
        if entry.context.message_id
    }
    for message in run.messages:
        context = context_from_inbound(message)
        if not context.timestamp and context.message_id in recorded_times:
            context = replace(context, timestamp=recorded_times[context.message_id])
        entries.append(_from_inbound(message, context, window, input_role))
    by_id, without_id = _unique_entries(entries)
    quotes = await _resolve_quotes(
        _referenced_ids(run), by_id, address, window, find_turn
    )
    entries = sorted(
        (
            entry
            for entry in (*by_id.values(), *without_id)
            if window.capture_observations
            or not entry.observed_only
            or entry.context.message_id in quotes
        ),
        key=_event_order,
    )
    heading = "## Group chat timeline (oldest first)"
    if run.attention:
        heading += "\nReason for this run: consider joining the group conversation."
        reply_to = run.attention.reply_to_message_id
        if reply_to:
            heading += (
                f"\nIf you reply, delivery quotes message_id {json.dumps(reply_to)}."
            )
        elif reply_to == "":
            heading += "\nIf you reply, delivery is a group comment without a quote."
    else:
        heading += "\nReason for this run: respond to the current_request entries."
    lines = _render_window(entries, quotes, window)
    return GroupTimeline(heading, lines, window.snapshot_at)


async def _resolve_quotes(
    roots: tuple[str, ...],
    by_id: dict[str, _Entry],
    address: MessageContext,
    window: TimelineWindow,
    find_turn: Callable[[str], Awaitable[MemoryRecord | None]] | None,
) -> dict[str, int]:
    """Resolve the nearest quote links first, with a bounded exact-session lookup."""
    pending = deque((message_id, 1) for message_id in roots)
    visited: set[str] = set()
    quotes: dict[str, int] = {}
    while pending and len(visited) < _MAX_QUOTE_TARGETS:
        message_id, depth = pending.popleft()
        if message_id in visited:
            continue
        visited.add(message_id)
        entry = by_id.get(message_id)
        if entry is None and find_turn is not None:
            try:
                record = await find_turn(message_id)
            except Exception:
                logger.warning(
                    "group_timeline.quote_lookup_failed",
                    message_id=message_id,
                    exc_info=True,
                )
                find_turn = None
                record = None
            if record is not None:
                candidate = _from_record(record, address, window)
                if candidate is not None and candidate.context.message_id == message_id:
                    entry = by_id[message_id] = candidate
        if entry is None:
            continue
        quotes[message_id] = depth
        parent_id = entry.context.reply_to_message_id
        if parent_id and depth < _MAX_QUOTE_DEPTH:
            pending.append((parent_id, depth + 1))
    return quotes


def _same_group(context: MessageContext, address: MessageContext) -> bool:
    return (
        context.chat_type == "group"
        and context.channel == address.channel
        and context.chat_id == address.chat_id
    )


def _from_record(
    record: MemoryRecord,
    address: MessageContext,
    window: TimelineWindow,
) -> _Entry | None:
    turn = record.turn
    if (
        turn.role not in {"user", "assistant"}
        or turn.source in {"node", "steer"}
        or turn.created_at > window.snapshot_at
    ):
        return None
    context = message_context_from_metadata(turn.metadata)
    if context is None or (turn.role == "assistant" and context.channel == "bot"):
        context = MessageContext(
            channel=address.channel,
            chat_type="group",
            chat_id=address.chat_id,
            sender_display_name="bot" if turn.role == "assistant" else "",
        )
    if not _same_group(context, address):
        return None
    metadata = turn.metadata or {}
    message_id = context.message_id or str(metadata.get("message_id") or "")
    event_time = _timestamp(context, turn.created_at, window.snapshot_at)
    if window.reset_at is not None and event_time < window.reset_at:
        return None
    context = replace(
        context,
        message_id=message_id,
        timestamp=event_time.timestamp(),
        reply_to_message_id=context.reply_to_message_id
        or str(metadata.get("reply_to") or ""),
    )
    return _Entry(
        event_time,
        context,
        _with_attachment_descriptions(turn.content, metadata.get("attachments")),
        "bot_message" if turn.role == "assistant" else "group_message",
        observed_only=metadata.get("observed_only") is True,
        media_metadata=metadata if metadata.get("attachments") else None,
    )


def _from_inbound(
    message: InboundMessage,
    context: MessageContext,
    window: TimelineWindow,
    role: str,
) -> _Entry:
    event_time = _timestamp(context, window.snapshot_at, window.snapshot_at)
    context = replace(context, timestamp=event_time.timestamp())
    if role == "group_message":
        text = _with_attachment_descriptions(
            message.text,
            [
                {"kind": item.kind, "alt_text": item.alt_text}
                for item in message.attachments
            ],
        )
        return _Entry(event_time, context, text, role)
    return _Entry(event_time, context, message.text, role, tuple(message.attachments))


def _timestamp(
    context: MessageContext, fallback: datetime, snapshot: datetime
) -> datetime:
    try:
        if context.timestamp > 0:
            event_time = datetime.fromtimestamp(context.timestamp, tz=UTC)
            if event_time <= snapshot:
                return event_time
    except (OverflowError, OSError, ValueError):
        pass
    return fallback


def _with_attachment_descriptions(text: str, attachments: Any) -> str:
    if not isinstance(attachments, list):
        return text
    notes = []
    for attachment in attachments:
        if not isinstance(attachment, dict) or attachment.get("kind") != "image":
            continue
        description = attachment.get("description") or attachment.get("alt_text")
        if isinstance(description, str) and description.strip():
            notes.append(f"[Image description: {description.strip()[:512]}]")
        else:
            notes.append("[Image attached. No saved description.]")
    return "\n".join([*notes, text])


def _unique_entries(entries: list[_Entry]) -> tuple[dict[str, _Entry], list[_Entry]]:
    by_id: dict[str, _Entry] = {}
    without_id = []
    for entry in entries:
        message_id = entry.context.message_id
        if not message_id:
            without_id.append(entry)
            continue
        previous = by_id.get(message_id)
        if (
            previous is None
            or entry.is_input
            or (entry.role == "bot_message" and previous.role == "group_message")
        ):
            by_id[message_id] = entry
    return by_id, without_id


def _event_order(entry: _Entry) -> tuple[datetime, int]:
    return _message_order(entry.context.channel, entry.time, entry.context.message_id)


def _message_order(
    channel: str, sent_at: datetime, message_id: str
) -> tuple[datetime, int]:
    if (
        channel == "milky"
        and len(message_id) < 25
        and message_id.isascii()
        and message_id.isdecimal()
    ):
        # Milky inbound times have second precision, whereas local acks do not.
        return sent_at.replace(microsecond=0), int(message_id)
    return sent_at, 0


def order_group_history(
    records: list[MemoryRecord], channel: str
) -> list[tuple[MemoryRecord, datetime]]:
    """Present a retrieved slice in message order and collapse repeated platform IDs."""
    snapshot_at = datetime.now(UTC)
    unique: dict[str, tuple[MemoryRecord, datetime]] = {}
    legacy: list[tuple[MemoryRecord, datetime]] = []
    for record in records:
        turn = record.turn
        metadata = turn.metadata or {}
        context = message_context_from_metadata(metadata) or MessageContext()
        sent_at = _timestamp(context, turn.created_at, snapshot_at)
        message_id = context.message_id or str(metadata.get("message_id") or "")
        if not message_id:
            legacy.append((record, sent_at))
            continue
        previous = unique.get(message_id)
        if previous is None or (
            turn.source == "group_delivery"
            and previous[0].turn.source != "group_delivery"
        ):
            unique[message_id] = (record, sent_at)
    rows = [
        (_message_order(channel, sent_at, message_id), record)
        for message_id, (record, sent_at) in unique.items()
    ]
    rows.extend(((sent_at, 0), record) for record, sent_at in legacy)
    rows.sort(key=lambda item: (*item[0], item[1].turn_id))
    return [(record, order[0]) for order, record in rows]


def _render_window(
    entries: list[_Entry], quotes: dict[str, int], window: TimelineWindow
) -> tuple[TimelineLine, ...]:
    selected = {
        index: entry.render() for index, entry in enumerate(entries) if entry.is_input
    }
    pinned = [
        (index, entry)
        for index, entry in enumerate(entries)
        if not entry.is_input and entry.context.message_id in quotes
    ]
    remaining = window.max_chars
    if pinned:
        quote_remaining = window.max_chars // 2
        quote_budget = min(1000, quote_remaining // len(pinned))
        for index, entry in sorted(
            pinned, key=lambda item: quotes[item[1].context.message_id]
        ):
            line = entry.render(quote_budget)
            size = len(line.text) + 2
            # Keep direct references even with a tiny budget. Ancestors share
            # the half reserved for quotes so recent group context can survive.
            if quotes[entry.context.message_id] > 1 and size > quote_remaining:
                continue
            selected[index] = replace(
                line,
                quote_depth=quotes[entry.context.message_id],
                media_metadata=entry.media_metadata,
            )
            remaining -= size
            quote_remaining -= size

    ttl = (
        window.snapshot_at - timedelta(seconds=window.ttl_seconds)
        if window.ttl_seconds
        else None
    )
    previous_time = window.snapshot_at
    count = 0
    for index in range(len(entries) - 1, -1, -1):
        entry = entries[index]
        if ttl is not None and entry.time < ttl:
            break
        if (
            window.topic_gap_seconds
            and (previous_time - entry.time).total_seconds() > window.topic_gap_seconds
        ):
            break
        previous_time = entry.time
        if index in selected:
            continue
        if count >= window.max_messages or remaining <= 0:
            break
        line = entry.render(remaining - 2)
        if len(line.text) + 2 > remaining:
            break
        selected[index] = line
        remaining -= len(line.text) + 2
        count += 1
    return tuple(selected[index] for index in sorted(selected))
