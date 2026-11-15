"""VTODO provider using ETags and stable operation keys for task commands."""

from typing import Any

from nahida_bot.plugins.todo.models import TaskCreate, TaskUpdate
from .config import TaskBinding
from .records import (
    assert_editable,
    edit_task,
    fingerprint,
    new_task,
    parse,
    stamp,
    task_values,
)
from .runtime import CalDAVRuntime
from .transport import CalDAVError, DavClient, Resource


class TaskProvider:
    def __init__(self, runtime: CalDAVRuntime) -> None:
        self.runtime = runtime

    async def handle(
        self, operation: str, args: dict[str, Any], owner: str
    ) -> dict[str, Any]:
        async with self.runtime.connection(owner, "tasks") as (client, binding, zone):
            assert isinstance(binding, TaskBinding)
            collection = await client.collection(binding.collection, "VTODO")
            if operation == "list":
                if args:
                    raise ValueError(
                        "Task list does not accept arbitrary source arguments"
                    )
                resources = await client.query(collection, "VTODO")
                items = [
                    await self.project(
                        owner, binding.connection, collection, resource, zone
                    )
                    for resource in resources
                ]
                return {"items": items}
            if operation == "create":
                request = TaskCreate.model_validate(args)
                values = request.model_dump(exclude={"operation_id"})
                uid = self.runtime.new_uid(
                    owner, binding.connection, collection, request.operation_id
                )
                resource = await create_once(
                    client,
                    collection + uid + ".ics",
                    new_task(uid, values, request.operation_id),
                    "VTODO",
                    fingerprint(values),
                )
            elif operation == "update":
                request = TaskUpdate.model_validate(args)
                resource = await self.runtime.resolve(
                    owner,
                    request.task_id,
                    binding.connection,
                    collection,
                    client,
                    "VTODO",
                )
                calendar, components = parse(resource.data, "VTODO")
                item = assert_editable(components)
                values = request.model_dump(
                    exclude={"task_id", "expected_revision", "operation_id"},
                    exclude_unset=True,
                )
                digest = fingerprint(values)
                if not repeated(item, request.operation_id, digest):
                    if resource.etag != request.expected_revision:
                        raise CalDAVError("revision_conflict_refresh_before_retry")
                    edit_task(item, values)
                    stamp(item, request.operation_id, digest)
                    resource = await client.put(
                        resource.href, calendar.to_ical(), etag=resource.etag
                    )
            else:
                raise ValueError("Unknown task provider operation")
            return {
                "task": await self.project(
                    owner, binding.connection, collection, resource, zone
                )
            }

    async def project(
        self,
        owner: str,
        connection: str,
        collection: str,
        resource: Resource,
        zone: str,
    ) -> dict[str, Any]:
        record_id, items = await self.runtime.remember(
            owner, connection, collection, resource, "VTODO"
        )
        result = task_values(
            items[0],
            timezone=zone,
            task_id=record_id,
            owner=owner,
            revision=resource.etag,
        )
        result["editable"] = result["editable"] and len(items) == 1
        return result


async def create_once(
    client: DavClient, href: str, data: bytes, kind: str, digest: str
) -> Resource:
    try:
        return await client.put(href, data)
    except CalDAVError as exc:
        if exc.code != "revision_conflict_refresh_before_retry":
            raise
    existing = await client.get(href)
    _, components = parse(existing.data, kind)
    if str(components[0].get("X-NAHIDA-CREATE-HASH", "")) != digest:
        raise CalDAVError("operation_key_already_used_with_different_content")
    return existing


def repeated(item: Any, operation: str, digest: str) -> bool:
    if str(item.get("X-NAHIDA-OPERATION", "")) != operation:
        return False
    if str(item.get("X-NAHIDA-OPERATION-HASH", "")) != digest:
        raise CalDAVError("operation_key_already_used_with_different_content")
    return True
