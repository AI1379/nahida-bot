"""OneBot v11 event parsing and normalization."""

from __future__ import annotations

from typing import Any

from nahida_bot.channels.onebot.cq_code import parse_cq_code
from nahida_bot.channels.onebot.protocol import NormalizedEvent, OneBotSelf


def normalize_v11_event(raw: dict[str, Any]) -> NormalizedEvent:
    """Convert a v11 event dict into a NormalizedEvent."""
    post_type = str(raw.get("post_type", ""))

    event_type = _normalize_type(post_type, raw)
    sub_type = _normalize_sub_type(post_type, raw)
    message_segments = _extract_message_segments(raw)
    alt_message = str(raw.get("raw_message", raw.get("message", "")))

    self_data = OneBotSelf(
        platform="qq",
        user_id=str(raw.get("self_id", "")),
    )

    return NormalizedEvent(
        type=event_type,
        sub_type=sub_type,
        message_id=str(raw.get("message_id", "")),
        user_id=str(raw.get("user_id", raw.get("sender", {}).get("user_id", ""))),
        group_id=str(raw.get("group_id", "")),
        self=self_data,
        message=message_segments,
        alt_message=alt_message if isinstance(alt_message, str) else "",
        time=float(raw.get("time", 0)),
        raw=raw,
        extra=_extract_extra(post_type, raw),
    )


def _normalize_type(post_type: str, raw: dict[str, Any]) -> str:
    if post_type == "message":
        message_type = str(raw.get("message_type", ""))
        return f"message.{message_type}" if message_type else "message"
    if post_type == "notice":
        notice_type = str(raw.get("notice_type", ""))
        return f"notice.{notice_type}" if notice_type else "notice"
    if post_type == "request":
        request_type = str(raw.get("request_type", ""))
        return f"request.{request_type}" if request_type else "request"
    if post_type == "meta_event":
        meta_type = str(raw.get("meta_event_type", ""))
        return f"meta.{meta_type}" if meta_type else "meta_event"
    return post_type


def _normalize_sub_type(post_type: str, raw: dict[str, Any]) -> str:
    if post_type == "message":
        return str(raw.get("sub_type", ""))
    if post_type == "notice":
        return str(raw.get("sub_type", ""))
    if post_type == "request":
        return str(raw.get("sub_type", ""))
    return ""


def _extract_message_segments(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract normalized message segment array, preferring structured format."""
    message = raw.get("message")
    if isinstance(message, list):
        segments = []
        for item in message:
            if isinstance(item, dict):
                segments.append(
                    {
                        "type": item.get("type", ""),
                        "data": item.get("data", {})
                        if isinstance(item.get("data"), dict)
                        else {},
                    }
                )
        if segments:
            return segments

    # Fallback to CQ code parsing. Some implementations provide CQ text in
    # ``message`` without also filling ``raw_message``.
    raw_message = raw.get("raw_message")
    if isinstance(raw_message, str) and raw_message:
        cq_segments = parse_cq_code(raw_message)
        return [{"type": seg.type, "data": seg.data} for seg in cq_segments]

    if isinstance(message, str) and message:
        cq_segments = parse_cq_code(message)
        return [{"type": seg.type, "data": seg.data} for seg in cq_segments]

    return []


def _extract_extra(post_type: str, raw: dict[str, Any]) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if post_type == "notice":
        extra["notice_type"] = raw.get("notice_type", "")
        extra["sub_type"] = raw.get("sub_type", "")
        extra["operator_id"] = raw.get("operator_id", "")
    if post_type == "request":
        extra["request_type"] = raw.get("request_type", "")
        extra["comment"] = raw.get("comment", "")
        extra["flag"] = raw.get("flag", "")
    if post_type == "meta_event":
        extra["meta_event_type"] = raw.get("meta_event_type", "")
        extra["status"] = raw.get("status", {})
        extra["interval"] = raw.get("interval", 0)
    return extra
