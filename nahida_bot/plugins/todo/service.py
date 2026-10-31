"""Account-scoped task storage with optimistic concurrency and create deduplication."""

from datetime import UTC, date, datetime
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid5

from nahida_bot_sdk.api import BotAPI

from .models import Task, TaskCreate, TaskUpdate


class TaskService:
    def __init__(self, api: BotAPI) -> None:
        self.api = api

    @staticmethod
    def prefix(owner: str) -> str:
        if not owner:
            raise PermissionError("Task ownership requires an authenticated account")
        return f"task:{sha256(owner.encode()).hexdigest()}:"

    async def list(self, owner: str, *, day: date | None = None) -> list[Task]:
        values = await self.api.plugin_data_list(self.prefix(owner))
        tasks = [Task.model_validate(value) for value in values.values()]
        tasks = [
            t
            for t in tasks
            if t.owner == owner and (day is None or t.belongs_to_day(day))
        ]
        return sorted(
            tasks,
            key=lambda t: (t.status != "open", t.due_date or date.max, t.created_at),
        )

    async def create(self, owner: str, request: TaskCreate) -> Task:
        task_id = uuid5(NAMESPACE_URL, f"{owner}:{request.operation_id}").hex
        key = self.prefix(owner) + task_id
        fingerprint = sha256(
            request.model_dump_json(exclude={"operation_id"}).encode()
        ).hexdigest()
        now = datetime.now(UTC)
        task = Task(
            id=task_id,
            owner=owner,
            title=request.title,
            planned_on=request.planned_on,
            due_date=request.due_date,
            created_at=now,
            updated_at=now,
            operation_id=request.operation_id,
            creation_fingerprint=fingerprint,
        )
        if await self.api.plugin_data_compare_and_set(
            key, None, task.model_dump(mode="json")
        ):
            return task
        existing = Task.model_validate(await self.api.plugin_data_get(key))
        if existing.creation_fingerprint != fingerprint:
            raise ValueError("Operation ID already used for another task")
        return existing

    async def update(self, owner: str, request: TaskUpdate) -> Task:
        key = self.prefix(owner) + request.task_id
        raw = await self.api.plugin_data_get(key)
        if raw is None or raw.get("owner") != owner:
            raise ValueError("Task not found")
        task = Task.model_validate(raw)
        updates = request.model_dump(
            exclude={"task_id", "expected_revision"}, exclude_unset=True
        )
        if task.operation_id == request.operation_id:
            if any(getattr(task, name) != value for name, value in updates.items()):
                raise ValueError("Operation ID already used with different changes")
            return task
        if task.revision != request.expected_revision:
            raise ValueError("Task changed elsewhere; refresh before editing")
        if (
            updates.get("title", task.title) is None
            or updates.get("status", task.status) is None
        ):
            raise ValueError("Title and status cannot be null")
        updated = task.model_copy(
            update={
                **updates,
                "revision": task.revision + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        if not await self.api.plugin_data_compare_and_set(
            key, raw, updated.model_dump(mode="json")
        ):
            raise ValueError("Task changed elsewhere; refresh before editing")
        return updated
