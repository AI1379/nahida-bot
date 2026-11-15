"""Task facade: keep local items and route external writes to their configured source."""

from datetime import date

from nahida_bot_sdk.api import BotAPI
from .models import Task, TaskCreate, TaskUpdate
from .service import TaskService


class TaskSources:
    def __init__(
        self, api: BotAPI, backends: dict[str, str], timezone: str = "Asia/Shanghai"
    ) -> None:
        self.api, self.backends = api, backends
        self.local = TaskService(api, timezone)

    async def list(self, owner: str, *, day: date | None = None) -> list[Task]:
        tasks = await self.local.list(owner, day=day)
        if service := self.backends.get(owner):
            result = await self.api.call_plugin_service(
                service, "list", {}, actor_account_key=owner
            )
            external = [Task.model_validate(item) for item in result["items"]]
            if any(task.owner != owner for task in external):
                raise PermissionError("Task provider returned data for another account")
            tasks.extend(
                task for task in external if day is None or task.belongs_to_day(day)
            )
        return tasks

    async def create(self, owner: str, request: TaskCreate) -> Task:
        if service := self.backends.get(owner):
            result = await self.api.call_plugin_service(
                service,
                "create",
                request.model_dump(mode="json"),
                actor_account_key=owner,
            )
            return Task.model_validate(result["task"])
        if request.remind_at is not None:
            raise ValueError(
                "Timed task alarms require a CalDAV reminder source; local TODO does not schedule alarms"
            )
        return await self.local.create(owner, request)

    async def update(self, owner: str, request: TaskUpdate) -> Task:
        if not request.task_id.startswith("ext_"):
            if request.remind_at is not None:
                raise ValueError("Timed task alarms require a CalDAV reminder source")
            return await self.local.update(owner, request)
        service = self.backends.get(owner)
        if not service:
            raise ValueError("The external task source is no longer configured")
        result = await self.api.call_plugin_service(
            service,
            "update",
            request.model_dump(mode="json", exclude_unset=True),
            actor_account_key=owner,
        )
        return Task.model_validate(result["task"])
