"""iCalendar parsing and edits preserve properties outside the requested change."""

from datetime import UTC, date, datetime
from hashlib import sha256
import json
from typing import Any
from zoneinfo import ZoneInfo

from icalendar import Alarm, Calendar, Event, Todo

from nahida_bot.plugins.calendar.models import EventCreate, calendar_time, validate_span
from .transport import CalDAVError


def fingerprint(values: dict[str, Any]) -> str:
    return sha256(
        json.dumps(values, sort_keys=True, default=str, ensure_ascii=False).encode()
    ).hexdigest()


def parse(data: bytes, kind: str) -> tuple[Calendar, list[Any]]:
    try:
        calendar = Calendar.from_ical(data)
        components = calendar.walk(kind)
        if not components or any(not item.get("UID") for item in components):
            raise ValueError("Missing UID or component")
        return calendar, components
    except Exception:
        raise CalDAVError("invalid_calendar_record") from None


def decoded(item: Any, key: str) -> Any:
    return item.decoded(key) if key in item else None


def replace(item: Any, key: str, value: Any) -> None:
    item.pop(key, None)
    if value is not None:
        item.add(key, value)


def envelope(component: Any) -> Calendar:
    calendar = Calendar()
    calendar.add("PRODID", "-//Nahida Bot//Personal Planning//EN")
    calendar.add("VERSION", "2.0")
    calendar.add_component(component)
    return calendar


def stamp(item: Any, operation: str, digest: str) -> None:
    replace(item, "DTSTAMP", datetime.now(UTC))
    replace(item, "LAST-MODIFIED", datetime.now(UTC))
    replace(item, "X-NAHIDA-OPERATION", operation)
    replace(item, "X-NAHIDA-OPERATION-HASH", digest)


def assert_editable(components: list[Any]) -> Any:
    if len(components) != 1 or any(
        key in components[0] for key in ("RRULE", "RDATE", "RECURRENCE-ID")
    ):
        raise CalDAVError("recurring_item_edit_requires_native_calendar")
    item = components[0]
    if "ATTENDEE" in item or "ORGANIZER" in item:
        raise CalDAVError("meeting_invitation_edit_not_supported")
    return item


def task_values(
    item: Any, *, timezone: str, task_id: str, owner: str, revision: str
) -> dict[str, Any]:
    due_date, due_at = task_deadline(item, timezone)
    planned = str(item.get("X-NAHIDA-PLANNED-ON", "")) or None
    if planned:
        date.fromisoformat(planned)
    created, updated = record_times(item, timezone)
    alarm_time = first_alarm(item, timezone)
    state = str(item.get("STATUS", "NEEDS-ACTION")).upper()
    return {
        "id": task_id,
        "owner": owner,
        "source": "caldav",
        "title": str(item.get("SUMMARY", "无标题")),
        "description": str(item.get("DESCRIPTION", "")),
        "planned_on": planned,
        "due_date": due_date,
        "due_at": due_at,
        "remind_at": alarm_time.isoformat() if alarm_time else None,
        "timezone": timezone,
        "priority": int(item.get("PRIORITY", 0)),
        "status": {"COMPLETED": "done", "CANCELLED": "cancelled"}.get(state, "open"),
        "revision": revision,
        "created_at": created.isoformat(),
        "updated_at": updated.isoformat(),
        "operation_id": str(item.get("X-NAHIDA-OPERATION", "")),
        "creation_fingerprint": str(item.get("X-NAHIDA-CREATE-HASH", "")),
        "editable": not any(
            key in item
            for key in ("RRULE", "RDATE", "RECURRENCE-ID", "ATTENDEE", "ORGANIZER")
        ),
    }


def normalized_time(value: Any, timezone: str) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=ZoneInfo(timezone))
    if isinstance(value, date):
        from datetime import time

        return datetime.combine(value, time.min, ZoneInfo(timezone))
    raise CalDAVError("invalid_calendar_timestamp")


def task_deadline(item: Any, timezone: str) -> tuple[str | None, str | None]:
    due = decoded(item, "DUE")
    if due is None:
        return None, None
    if isinstance(due, datetime):
        return None, normalized_time(due, timezone).isoformat()
    if isinstance(due, date):
        return due.isoformat(), None
    raise CalDAVError("invalid_task_deadline")


def record_times(item: Any, timezone: str) -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    created = decoded(item, "CREATED") or decoded(item, "DTSTAMP") or now
    updated = decoded(item, "LAST-MODIFIED") or decoded(item, "DTSTAMP") or created
    return normalized_time(created, timezone), normalized_time(updated, timezone)


def first_alarm(item: Any, timezone: str) -> datetime | None:
    from datetime import timedelta

    for alarm in item.subcomponents:
        if alarm.name != "VALARM" or "TRIGGER" not in alarm:
            continue
        trigger = alarm.decoded("TRIGGER")
        if isinstance(trigger, datetime):
            return normalized_time(trigger, timezone)
        anchor = "DUE" if alarm["TRIGGER"].params.get("RELATED") == "END" else "DTSTART"
        value = decoded(item, anchor)
        if isinstance(trigger, timedelta) and value is not None:
            return normalized_time(value, timezone) + trigger
    return None


def edit_task(item: Any, values: dict[str, Any]) -> None:
    for field, key in (
        ("title", "SUMMARY"),
        ("description", "DESCRIPTION"),
        ("priority", "PRIORITY"),
        ("planned_on", "X-NAHIDA-PLANNED-ON"),
    ):
        if field in values:
            value = values[field]
            replace(
                item,
                key,
                value.isoformat() if field == "planned_on" and value else value,
            )
    if "due_at" in values or "due_date" in values:
        deadline = values.get("due_at") or values.get("due_date")
        replace(
            item,
            "DUE",
            deadline.astimezone(UTC) if isinstance(deadline, datetime) else deadline,
        )
    if "status" in values:
        state = values["status"]
        replace(
            item,
            "STATUS",
            {"open": "NEEDS-ACTION", "done": "COMPLETED", "cancelled": "CANCELLED"}[
                state
            ],
        )
        replace(item, "COMPLETED", datetime.now(UTC) if state == "done" else None)
        replace(item, "PERCENT-COMPLETE", 100 if state == "done" else 0)
    if "remind_at" in values:
        # Explicit alarm edits replace alarms. Other task edits preserve all alarms.
        item.subcomponents = [
            part for part in item.subcomponents if part.name != "VALARM"
        ]
        if values["remind_at"] is not None:
            alarm = Alarm()
            alarm.add("ACTION", "DISPLAY")
            alarm.add("DESCRIPTION", str(item.get("SUMMARY", "提醒")))
            alarm.add("TRIGGER", values["remind_at"].astimezone(UTC))
            item.add_component(alarm)


def new_task(uid: str, values: dict[str, Any], operation: str) -> bytes:
    item = Todo()
    item.add("UID", uid)
    item.add("CREATED", datetime.now(UTC))
    item.add("STATUS", "NEEDS-ACTION")
    edit_task(item, values)
    digest = fingerprint(values)
    item.add("X-NAHIDA-CREATE-HASH", digest)
    stamp(item, operation, digest)
    return envelope(item).to_ical()


def event_values(item: Any) -> dict[str, Any]:
    start, end = decoded(item, "DTSTART"), decoded(item, "DTEND")
    if start is None:
        raise CalDAVError("event_without_start")
    if end is None:
        from datetime import timedelta

        duration = decoded(item, "DURATION")
        end = start + (
            duration
            or (timedelta(0) if isinstance(start, datetime) else timedelta(days=1))
        )
    try:
        if type(start) is not type(end) or end < start:
            raise ValueError("Invalid span")
    except (TypeError, ValueError):
        raise CalDAVError("invalid_event_span") from None
    return {
        "title": str(item.get("SUMMARY", "无标题")),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "all_day": not isinstance(start, datetime),
        "description": str(item.get("DESCRIPTION", "")),
        "location": str(item.get("LOCATION", "")),
        "busy": str(item.get("TRANSP", "OPAQUE")) != "TRANSPARENT",
        "status": str(item.get("STATUS", "CONFIRMED")).upper(),
        "task_id": str(item.get("X-NAHIDA-TASK-ID", "")),
    }


def edit_event(item: Any, values: dict[str, Any]) -> None:
    span = event_values(item) if "DTSTART" in item else {}
    span.update(
        {key: value for key, value in values.items() if key in {"start", "end"}}
    )
    validate_span(span["start"], span["end"])
    if "start" in values or "end" in values:
        item.pop("DURATION", None)
        for field, key in (("start", "DTSTART"), ("end", "DTEND")):
            moment = calendar_time(span[field])
            replace(
                item,
                key,
                moment.astimezone(UTC) if isinstance(moment, datetime) else moment,
            )
    for field, key in (
        ("title", "SUMMARY"),
        ("description", "DESCRIPTION"),
        ("location", "LOCATION"),
        ("task_id", "X-NAHIDA-TASK-ID"),
    ):
        if field in values:
            replace(item, key, values[field])
    if "busy" in values:
        replace(item, "TRANSP", "OPAQUE" if values["busy"] else "TRANSPARENT")


def new_event(uid: str, request: EventCreate) -> bytes:
    values = request.model_dump(exclude={"operation_id"})
    item = Event()
    item.add("UID", uid)
    item.add("CREATED", datetime.now(UTC))
    edit_event(item, values)
    digest = fingerprint(values)
    item.add("X-NAHIDA-CREATE-HASH", digest)
    stamp(item, request.operation_id, digest)
    return envelope(item).to_ical()
