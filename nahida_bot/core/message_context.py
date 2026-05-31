"""Helpers for stable per-turn message context envelopes."""

from __future__ import annotations

import re
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from nahida_bot.plugins.base import (
    ChatContext,
    InboundMessage,
    MessageContext,
    SenderContext,
)

# Regex to detect and strip timestamp/envelope prefixes the LLM may
# mistakenly emit despite ENVELOPE_INSTRUCTION.
#
# Matches patterns like:
#   2026-05-10 14:03 +08
#   [2026-05-10 14:03 +08]
#   [2026-05-10 14:03 +08 | milky/group:Chat | Alice admin]
#   2026-05-10 14:03 +08:00
#   2026-05-10T14:03+08:00
# followed by optional newlines and then the actual reply content.
_ENVELOPE_PREFIX_RE = re.compile(
    r"^"  # start of text
    r"\s*"  # optional leading whitespace
    r"\[?"  # optional opening bracket
    r"\d{4}-\d{2}-\d{2}"  # date: YYYY-MM-DD
    r"[T\s]+\d{2}:\d{2}"  # time: HH:MM (space or T separator)
    r"(?::\d{2})?"  # optional seconds
    r"(?:\s*[+-]\d{2}(?::?\d{2})?)?"  # optional tz: +08, +0800, +08:00
    r"(?:\s*\|[^\]\n]*)?"  # optional envelope body after |
    r"\]?"  # optional closing bracket
    r"[\s,;.:]*"  # trailing separator chars
    r"(?:\n)?",  # optional newline after prefix
)


def strip_envelope_prefix(text: str) -> str:
    """Remove a mistaken envelope/timestamp prefix from LLM output.

    The LLM is instructed not to reproduce envelope metadata, but some
    models occasionally emit a leading timestamp or full bracket tag
    anyway.  This function strips that prefix so the user never sees it.

    Returns the cleaned text (which may be empty if the entire text was
    just a timestamp).
    """
    if not text:
        return text
    return _ENVELOPE_PREFIX_RE.sub("", text, count=1).lstrip()


ENVELOPE_INSTRUCTION = (
    "## Message Metadata Tags\n"
    "Each incoming message is prefixed with a context tag in square brackets:\n"
    "[2026-05-10 14:03 +08 | milky/group:ChatName(chatid) | Alice admin]\n"
    "This is system-generated metadata, NOT part of the message itself.\n"
    "CRITICAL: Never produce, reproduce, or mimic these bracket tags in "
    "your own replies. Your responses must contain only plain reply text — "
    "no square-bracket metadata, no envelope formatting."
)

SILENT_REPLY_INSTRUCTION = (
    "## Silent Replies\n"
    "Use NO_REPLY ONLY when no user-visible reply is required.\n"
    "Rules:\n"
    "- Valid cases: silent housekeeping, deliberate no-op, after a tool "
    "already delivered the reply.\n"
    "- It must be your ENTIRE message — nothing else.\n"
    "- Never append it to an actual response.\n"
    "- Never wrap it in markdown or code blocks.\n"
    '- You may also use {"action": "NO_REPLY"} JSON format.'
)

HEARTBEAT_INSTRUCTION = (
    "## Heartbeats\n"
    "This is a scheduled periodic check-in. If nothing needs attention, "
    "reply exactly:\n"
    "HEARTBEAT_OK\n"
    'If something needs attention, do NOT include "HEARTBEAT_OK"; reply '
    "with the alert text instead.\n"
    '- You may also use {"action": "HEARTBEAT_OK"} JSON format.'
)


def context_from_inbound(inbound: InboundMessage) -> MessageContext:
    """Build a MessageContext from normalized inbound fields and channel facts."""
    if inbound.message_context is not None:
        return inbound.message_context

    sender = inbound.sender_context
    chat = inbound.chat_context
    return MessageContext(
        timestamp=inbound.timestamp,
        channel=chat.platform if chat and chat.platform else inbound.platform,
        chat_type=(
            chat.chat_type
            if chat and chat.chat_type
            else ("group" if inbound.is_group else "private")
        ),
        chat_id=chat.platform_chat_id
        if chat and chat.platform_chat_id
        else inbound.chat_id,
        chat_display_name=chat.display_name if chat else "",
        sender_id=(
            sender.platform_user_id
            if sender and sender.platform_user_id
            else inbound.user_id
        ),
        sender_display_name=sender.display_name if sender else "",
        sender_role_tags=sender.role_tags if sender else (),
        extra_tags=(),
    )


def assistant_context(*, timestamp: float | None = None) -> MessageContext:
    """Build a compact MessageContext for assistant turns."""
    ts = timestamp if timestamp is not None else datetime.now(UTC).timestamp()
    return MessageContext(
        timestamp=ts,
        channel="bot",
        chat_type="assistant",
        sender_display_name="bot",
        sender_role_tags=("bot",),
    )


def message_context_to_metadata(
    context: MessageContext | None,
) -> dict[str, Any] | None:
    """Serialize a MessageContext to a JSON-compatible metadata object."""
    if context is None:
        return None
    data = asdict(context)
    data["sender_role_tags"] = list(context.sender_role_tags)
    data["extra_tags"] = list(context.extra_tags)
    return data


def message_context_from_metadata(
    metadata: dict[str, Any] | None,
) -> MessageContext | None:
    """Recover a MessageContext from turn metadata."""
    if not metadata:
        return None
    raw = metadata.get("message_context")
    if not isinstance(raw, dict):
        return None
    return MessageContext(
        timestamp=_safe_float(raw.get("timestamp")),
        channel=str(raw.get("channel") or ""),
        chat_type=str(raw.get("chat_type") or "unknown"),
        chat_id=str(raw.get("chat_id") or ""),
        chat_display_name=str(raw.get("chat_display_name") or ""),
        sender_id=str(raw.get("sender_id") or ""),
        sender_display_name=str(raw.get("sender_display_name") or ""),
        sender_role_tags=_string_tuple(raw.get("sender_role_tags")),
        extra_tags=_string_tuple(raw.get("extra_tags")),
    )


def render_message_with_context(
    content: str,
    context: MessageContext | None,
    *,
    role: str = "",
) -> str:
    """Prepend a stable, compact envelope to a turn's LLM-visible content."""
    envelope = render_envelope(context, role=role)
    if not envelope:
        return content
    if not content:
        return envelope
    return f"{envelope}\n{content}"


def render_envelope(context: MessageContext | None, *, role: str = "") -> str:
    """Render MessageContext as a short single-line tag block."""
    if context is None:
        return ""

    parts = [
        _format_timestamp(context.timestamp),
        _format_channel(context),
        _format_sender(context, role=role),
    ]
    rendered = [part for part in parts if part]
    if not rendered:
        return ""
    return "[" + " | ".join(rendered) + "]"


def sender_context_from_values(
    *,
    display_name: str = "",
    platform_user_id: str = "",
    role_tags: tuple[str, ...] | list[str] = (),
    is_bot: bool = False,
    is_self: bool = False,
) -> SenderContext:
    """Create a sanitized SenderContext for channel converters."""
    return SenderContext(
        display_name=_clean(display_name),
        platform_user_id=_clean(platform_user_id),
        role_tags=_dedupe_tags(role_tags),
        is_bot=is_bot,
        is_self=is_self,
    )


def chat_context_from_values(
    *,
    platform: str,
    chat_type: str,
    platform_chat_id: str = "",
    display_name: str = "",
) -> ChatContext:
    """Create a sanitized ChatContext for channel converters."""
    return ChatContext(
        platform=_clean(platform),
        chat_type=_clean(chat_type) or "unknown",
        platform_chat_id=_clean(platform_chat_id),
        display_name=_clean(display_name),
    )


def _format_timestamp(timestamp: float) -> str:
    if timestamp <= 0:
        return ""
    dt = datetime.fromtimestamp(timestamp, tz=UTC).astimezone()
    offset = dt.strftime("%z")
    if len(offset) == 5 and offset.endswith("00"):
        offset = offset[:3]
    elif len(offset) == 5:
        offset = f"{offset[:3]}:{offset[3:]}"
    return f"{dt:%Y-%m-%d %H:%M} {offset}".strip()


def _format_channel(context: MessageContext) -> str:
    channel = _clean(context.channel)
    chat_type = _clean(context.chat_type)
    chat_name = _clean(context.chat_display_name)
    chat_id = _clean(context.chat_id)

    if channel and chat_type and chat_type != "unknown":
        base = f"{channel}/{chat_type}"
    else:
        base = channel or chat_type

    if chat_name and chat_id and chat_name != chat_id:
        return f"{base}:{chat_name}({chat_id})"
    if chat_id:
        return f"{base}:{chat_id}"
    return base


def _format_sender(context: MessageContext, *, role: str) -> str:
    if role == "assistant":
        base = "bot"
    else:
        name = _clean(context.sender_display_name)
        sid = _clean(context.sender_id)
        if name and sid and name != sid:
            base = f"{name}({sid})"
        else:
            base = name or sid
    if not base:
        base = role or "sender"

    tags = [tag for tag in context.sender_role_tags if tag and tag != base]
    tags.extend(tag for tag in context.extra_tags if tag)
    if not tags:
        return base
    return " ".join([base, *_dedupe_tags(tags)])


def _safe_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return _dedupe_tags(str(item) for item in value)


def _dedupe_tags(values: tuple[str, ...] | list[str] | object) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:  # type: ignore[union-attr]
        tag = _clean(str(value))
        if not tag or tag in seen:
            continue
        seen.add(tag)
        result.append(tag)
    return tuple(result)


def _clean(value: str) -> str:
    return " ".join(value.replace("|", " ").replace("\n", " ").split())
