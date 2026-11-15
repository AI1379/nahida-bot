"""Personal-event writes, kept separate from recurrence-aware calendar reads."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from nahida_bot.plugins.calendar.models import (
    EventCreate,
    EventMutation,
    EventUpdate,
    TimeWindow,
)
from .config import CalendarBinding
from .records import (
    assert_editable,
    edit_event,
    event_values,
    fingerprint,
    new_event,
    parse,
    stamp,
)
from .runtime import CalDAVRuntime
from .tasks import create_once, repeated
from .transport import CalDAVError, DavClient, Resource


@dataclass(frozen=True)
class EventScope:
    client: DavClient
    binding: CalendarBinding
    owner: str
    timezone: str
    collection: str = ""


class EventWriter:
    def __init__(
        self,
        runtime: CalDAVRuntime,
        scope: EventScope,
        read: Callable[[TimeWindow], Awaitable[list[dict[str, Any]]]],
    ) -> None:
        self.runtime, self.scope, self.read = runtime, scope, read

    async def handle(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if operation == "create":
            resource = await self.create(EventCreate.model_validate(arguments))
        elif operation == "update":
            resource = await self.update(EventUpdate.model_validate(arguments))
        elif operation == "delete":
            return await self.delete(EventMutation.model_validate(arguments))
        else:
            raise ValueError("Unknown calendar provider operation")
        record_id, items = await self.runtime.remember(
            self.scope.owner,
            self.scope.binding.connection,
            self.scope.collection,
            resource,
            "VEVENT",
        )
        return {
            "event": {
                "id": record_id,
                "revision": resource.etag,
                "source": self.scope.binding.connection,
                **event_values(items[0]),
            }
        }

    async def create(self, request: EventCreate) -> Resource:
        scope = self.scope
        uid = self.runtime.new_uid(
            scope.owner,
            scope.binding.connection,
            scope.collection,
            request.operation_id,
        )
        href = scope.collection + uid + ".ics"
        digest = fingerprint(request.model_dump(exclude={"operation_id"}))
        try:
            existing = await scope.client.get(href)
        except CalDAVError as exc:
            if exc.code != "resource_not_found":
                raise
        else:
            _, items = parse(existing.data, "VEVENT")
            if str(items[0].get("X-NAHIDA-CREATE-HASH", "")) != digest:
                raise CalDAVError("operation_key_already_used_with_different_content")
            return existing
        if request.busy and not request.allow_conflicts:
            await self.check_conflicts(request.start, request.end)
        return await create_once(
            scope.client, href, new_event(uid, request), "VEVENT", digest
        )

    async def update(self, request: EventUpdate) -> Resource:
        resource = await self.resolve(request.event_id)
        calendar, items = parse(resource.data, "VEVENT")
        item = assert_editable(items)
        values = request.model_dump(
            exclude={
                "event_id",
                "expected_revision",
                "operation_id",
                "allow_conflicts",
            },
            exclude_unset=True,
        )
        if any(value is None for value in values.values()):
            raise ValueError(
                "Use empty text to clear description/location; event fields cannot be null"
            )
        digest = fingerprint(values)
        if repeated(item, request.operation_id, digest):
            return resource
        if resource.etag != request.expected_revision:
            raise CalDAVError("revision_conflict_refresh_before_retry")
        edit_event(item, values)
        changed = event_values(item)
        if (
            changed["busy"]
            and not request.allow_conflicts
            and {"start", "end", "busy"}.intersection(values)
        ):
            await self.check_conflicts(
                changed["start"], changed["end"], excluding=request.event_id
            )
        stamp(item, request.operation_id, digest)
        return await self.scope.client.put(
            resource.href, calendar.to_ical(), etag=resource.etag
        )

    async def delete(self, request: EventMutation) -> dict[str, Any]:
        receipt_key = self.runtime.key(
            self.scope.owner, f"delete:{request.event_id}:{request.operation_id}"
        )
        if await self.runtime.api.plugin_data_get(receipt_key):
            return {"deleted": True, "event_id": request.event_id}
        resource = await self.resolve(request.event_id)
        _, items = parse(resource.data, "VEVENT")
        assert_editable(items)
        if resource.etag != request.expected_revision:
            raise CalDAVError("revision_conflict_refresh_before_retry")
        await self.scope.client.delete(resource.href, resource.etag)
        await self.runtime.api.plugin_data_set(receipt_key, {"deleted": True})
        return {"deleted": True, "event_id": request.event_id}

    async def resolve(self, event_id: str) -> Resource:
        scope = self.scope
        return await self.runtime.resolve(
            scope.owner,
            event_id,
            scope.binding.connection,
            scope.collection,
            scope.client,
            "VEVENT",
        )

    async def check_conflicts(
        self, start: str, end: str, *, excluding: str = ""
    ) -> None:
        from .event_reads import instant

        window = TimeWindow(
            start=instant(start, self.scope.timezone),
            end=instant(end, self.scope.timezone),
        )
        events = await self.read(window)
        for event in events:
            if (
                event["id"] == excluding
                or not event["busy"]
                or event["status"] == "CANCELLED"
            ):
                continue
            if instant(event["end"], self.scope.timezone) > instant(
                event["start"], self.scope.timezone
            ):
                raise CalDAVError(
                    "calendar_conflict_choose_another_time_or_explicitly_allow"
                )
