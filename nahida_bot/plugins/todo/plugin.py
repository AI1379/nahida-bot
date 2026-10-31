"""Task tools, service and Desktop projections owned by the TODO plugin."""

from datetime import date, datetime
import json
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from nahida_bot_sdk.desktop import (
    DesktopSurfaceAction,
    DesktopSurfaceContext,
    DesktopSurfaceItem,
    DesktopSurfaceView,
)
from nahida_bot_sdk.plugin import Plugin
from nahida_bot_sdk.services import PluginServiceContext

from .models import TaskCreate, TaskUpdate
from .service import TaskService


class TodoPlugin(Plugin):
    async def on_load(self) -> None:
        self.tasks = TaskService(self.api)
        self.timezone = ZoneInfo(self.manifest.config.get("timezone", "Asia/Shanghai"))
        self.api.register_plugin_service("tasks.v1", self.handle, desktop=True)
        for name in ("today", "drawer"):
            self.api.register_desktop_surface_provider(name, self.surface)
        self.api.register_tool(
            "todo_list",
            "List your stored personal tasks. Completed tasks remain available.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            self.list_tool,
        )
        self.api.register_tool(
            "todo_add",
            "Create a personal task when the user asks. planned_on is a plan date, due_date is a deadline.",
            self.tool_schema(TaskCreate),
            self.create_tool,
        )
        self.api.register_tool(
            "todo_update",
            "Update your task using its ID and revision from todo_list. Closing a reminder does not complete tasks.",
            self.tool_schema(TaskUpdate),
            self.update_tool,
        )

    @staticmethod
    def tool_schema(model: type[TaskCreate] | type[TaskUpdate]) -> dict[str, Any]:
        schema = model.model_json_schema()
        schema["required"] = [
            name for name in schema["required"] if name != "operation_id"
        ]
        schema["properties"]["operation_id"]["description"] = (
            "Optional idempotency key. Reuse it for retries of the same action; omit for a new action."
        )
        return schema

    async def on_enable(self) -> None:
        self.api.spawn_interval_task("refresh-today", self.refresh, interval_seconds=60)

    async def refresh(self) -> None:
        for surface_id in ("today", "drawer"):
            self.api.request_desktop_surface_refresh(surface_id)

    async def handle(
        self, operation: str, arguments: dict[str, Any], context: PluginServiceContext
    ) -> dict[str, Any]:
        owner = context.actor_account_key
        if operation in ("list", "today"):
            allowed = {"date"} if operation == "today" else set()
            if set(arguments) - allowed:
                raise ValueError("Unknown list arguments")
            day = (
                date.fromisoformat(arguments["date"])
                if arguments.get("date")
                else datetime.now(self.timezone).date()
            )
            tasks = await self.tasks.list(
                owner, day=day if operation == "today" else None
            )
            return {"items": [task.model_dump(mode="json") for task in tasks]}
        if operation == "create":
            task = await self.tasks.create(owner, TaskCreate.model_validate(arguments))
        elif operation == "update":
            task = await self.tasks.update(owner, TaskUpdate.model_validate(arguments))
        else:
            raise ValueError("Unknown task operation")
        await self.refresh()
        return {"task": task.model_dump(mode="json")}

    async def surface(
        self, context: DesktopSurfaceContext
    ) -> DesktopSurfaceView | None:
        if not context.actor_account_key:
            return None
        day = datetime.now(self.timezone).date()
        tasks = await self.tasks.list(context.actor_account_key, day=day)
        return DesktopSurfaceView(
            title="今日待办",
            status=f"{day.isoformat()} · {len(tasks)} 项",
            text="今天还没有安排。可以在聊天中添加待办。"
            if not tasks
            else "计划做的事与截止日期分别记录。",
            items=[
                DesktopSurfaceItem(
                    text=t.title,
                    detail=f"截止 {t.due_date}" if t.due_date else "今日计划",
                    action=DesktopSurfaceAction(
                        label="完成",
                        service="tasks.v1",
                        operation="update",
                        arguments={
                            "task_id": t.id,
                            "expected_revision": t.revision,
                            "status": "done",
                        },
                    ),
                )
                for t in tasks[:20]
            ],
            detail="仅显示前 20 项；完整列表可用 todo_list 查看。"
            if len(tasks) > 20
            else "",
        )

    async def _tool(self, operation: str, arguments: dict[str, Any]) -> str:
        try:
            if operation != "list":
                arguments.setdefault("operation_id", uuid4().hex)
            result = await self.handle(
                operation, arguments, PluginServiceContext(self.api.actor_account_key)
            )
            return json.dumps(result, ensure_ascii=False)
        except (ValueError, PermissionError) as exc:
            return f"Error: {exc}"

    async def list_tool(self) -> str:
        return await self._tool("list", {})

    async def create_tool(self, **kwargs: Any) -> str:
        return await self._tool("create", kwargs)

    async def update_tool(self, **kwargs: Any) -> str:
        return await self._tool("update", kwargs)
