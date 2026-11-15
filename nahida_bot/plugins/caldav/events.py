"""Calendar orchestration over a bound connection; reads and writes stay separate."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from nahida_bot.plugins.calendar.models import AvailabilityRequest, TimeWindow
from .config import CalendarBinding
from .event_reads import availability, expanded_values
from .event_writes import EventScope, EventWriter
from .runtime import CalDAVRuntime
from .transport import CalDAVError


class EventProvider:
    def __init__(self, runtime: CalDAVRuntime) -> None:
        self.runtime = runtime

    async def handle(
        self, operation: str, args: dict[str, Any], owner: str
    ) -> dict[str, Any]:
        async with self.runtime.connection(owner, "calendar") as (
            client,
            binding,
            zone,
        ):
            assert isinstance(binding, CalendarBinding)
            scope = EventScope(client, binding, owner, zone)
            if operation in {"list", "availability"}:
                model = (
                    AvailabilityRequest if operation == "availability" else TimeWindow
                )
                window = model.model_validate(args)
                events = await self.list_events(scope, window)
                if isinstance(window, AvailabilityRequest):
                    return availability(events, window, zone)
                return {
                    "items": events,
                    "timezone": zone,
                    "as_of": datetime.now(UTC).isoformat(),
                }
            collection = await client.collection(binding.write_collection, "VEVENT")
            scope = EventScope(client, binding, owner, zone, collection)
            writer = EventWriter(
                self.runtime, scope, lambda window: self.list_events(scope, window)
            )
            return await writer.handle(operation, args)

    async def list_events(
        self, scope: EventScope, window: TimeWindow
    ) -> list[dict[str, Any]]:
        client, binding = scope.client, scope.binding
        paths = list(dict.fromkeys(binding.read_collections))
        if binding.write_collection and binding.write_collection not in paths:
            paths.append(binding.write_collection)
        if not paths:
            raise CalDAVError("read_calendars_not_configured")
        collections = await asyncio.gather(
            *(client.collection(path, "VEVENT") for path in paths)
        )
        # An offset-change margin avoids server-side DST boundary omissions.
        # The local recurrence expansion still returns only the exact window.
        period = (
            (window.start - timedelta(days=1))
            .astimezone(UTC)
            .strftime("%Y%m%dT%H%M%SZ"),
            (window.end + timedelta(days=1)).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ"),
        )
        groups = await asyncio.gather(
            *(
                client.query(collection, "VEVENT", time_range=period)
                for collection in collections
            )
        )
        writable = (
            collections[paths.index(binding.write_collection)]
            if binding.write_collection in paths
            else ""
        )
        events = []
        for collection, resources in zip(collections, groups, strict=True):
            for resource in resources:
                record_id, _ = await self.runtime.remember(
                    scope.owner, binding.connection, collection, resource, "VEVENT"
                )
                for item in expanded_values(resource, window, scope.timezone):
                    events.append(
                        {
                            **item,
                            "id": record_id,
                            "revision": resource.etag,
                            "source": binding.connection,
                            "editable": item["editable"] and collection == writable,
                        }
                    )
                if len(events) > 2000:
                    raise CalDAVError("too_many_occurrences_narrow_query")
        from .event_reads import instant

        return sorted(events, key=lambda item: instant(item["start"], scope.timezone))
