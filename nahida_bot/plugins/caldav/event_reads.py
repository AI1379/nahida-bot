"""Bounded recurrence expansion and busy/free calculations."""

from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import recurring_ical_events

from nahida_bot.plugins.calendar.models import (
    AvailabilityRequest,
    TimeWindow,
    calendar_time,
)
from .records import event_values, parse
from .transport import CalDAVError, Resource


def instant(value: str, zone: str) -> datetime:
    parsed = calendar_time(value) if len(value) == 10 else datetime.fromisoformat(value)
    if isinstance(parsed, datetime):
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo(zone))
    return datetime.combine(parsed, time.min, ZoneInfo(zone))


def expanded_values(
    resource: Resource, window: TimeWindow, zone: str
) -> list[dict[str, Any]]:
    calendar, original = parse(resource.data, "VEVENT")
    calendar.setdefault("X-WR-TIMEZONE", zone)
    recurring = len(original) > 1 or any(
        key in original[0] for key in ("RRULE", "RDATE", "RECURRENCE-ID")
    )
    reject_dense_rules(original)
    try:
        expanded = recurring_ical_events.of(calendar).between(window.start, window.end)
    except Exception:
        raise CalDAVError("cannot_expand_calendar_do_not_assume_free") from None
    result = []
    for item in expanded:
        values = event_values(item)
        if (
            instant(values["end"], zone) <= window.start
            or instant(values["start"], zone) >= window.end
        ):
            continue
        result.append(
            {
                "recurring": recurring,
                "editable": not recurring
                and not any(key in item for key in ("ATTENDEE", "ORGANIZER")),
                **values,
            }
        )
    return result


def reject_dense_rules(items: list[Any]) -> None:
    for item in items:
        rule = item.get("RRULE")
        if rule and any(
            str(freq) in {"SECONDLY", "MINUTELY"} for freq in rule.get("FREQ", [])
        ):
            raise CalDAVError("recurrence_too_dense_for_bounded_query")


def availability(
    events: list[dict[str, Any]], window: AvailabilityRequest, zone: str
) -> dict[str, Any]:
    busy = []
    for item in events:
        if not item["busy"] or item["status"] == "CANCELLED":
            continue
        start, end = instant(item["start"], zone), instant(item["end"], zone)
        if end > start:
            busy.append((max(window.start, start), min(window.end, end)))
    cursor = window.start
    free = []
    for start, end in [*sorted(busy), (window.end, window.end)]:
        if start - cursor >= timedelta(minutes=window.duration_minutes):
            free.append({"start": cursor.isoformat(), "end": start.isoformat()})
        cursor = max(cursor, end)
    return {
        "free_windows": free[:20],
        "truncated": len(free) > 20,
        "timezone": zone,
        "as_of": datetime.now(UTC).isoformat(),
        "reservation": False,
    }
