"""Workspace-backed task-plan tool for structured agent work."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import structlog

from nahida_bot.plugins.tooling import PluginToolDefinition
from nahida_bot_sdk.api import BotAPI


_logger = structlog.get_logger(__name__)
_PLAN_PATH = ".agent/plan.json"
_VALID_STATUSES = frozenset({"pending", "in_progress", "completed", "failed"})

_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["create", "list", "update", "add", "remove", "clear"],
            "description": "The action to perform on the plan.",
        },
        "title": {
            "type": "string",
            "description": "Plan title (used with 'create').",
        },
        "tasks": {
            "type": "array",
            "description": (
                "Tasks for 'create' or 'add'. Each has title and optional detail."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                },
                "required": ["title"],
            },
        },
        "task_id": {
            "type": "integer",
            "description": "Task ID for 'update' or 'remove'.",
        },
        "status": {
            "type": "string",
            "description": (
                "New status for 'update': pending, in_progress, completed, failed."
            ),
        },
        "detail": {
            "type": "string",
            "description": "New detail text for 'update'.",
        },
    },
    "required": ["action"],
    "additionalProperties": False,
}


@dataclass(slots=True, frozen=True)
class _PlanRequest:
    action: str
    title: str = ""
    tasks: tuple[dict[str, str], ...] = ()
    task_id: int | None = None
    status: str = ""
    detail: str = ""

    @classmethod
    def from_arguments(cls, arguments: dict[str, Any]) -> _PlanRequest:
        """Normalize provider arguments into one immutable request."""
        return cls(
            action=str(arguments.get("action", "")),
            title=str(arguments.get("title", "")),
            tasks=tuple(arguments.get("tasks") or ()),
            task_id=arguments.get("task_id"),
            status=str(arguments.get("status", "")),
            detail=str(arguments.get("detail", "")),
        )


PlanAction = Callable[[_PlanRequest], Awaitable[str]]


class PlanTools:
    """Define and execute a durable plan stored in the active workspace."""

    def __init__(self, api: BotAPI) -> None:
        self._api = api

    def definitions(self) -> tuple[PluginToolDefinition, ...]:
        """Return the plan tool exposed to the model."""
        return (
            PluginToolDefinition(
                name="plan",
                description=(
                    "Create and manage a task plan for structured work. "
                    "Actions: create, list, update, add, remove, clear."
                ),
                parameters=_PARAMETERS,
                handler=self.execute,
            ),
        )

    async def execute(self, **arguments: Any) -> str:
        """Dispatch one plan operation."""
        request = _PlanRequest.from_arguments(arguments)
        _logger.debug("tool.plan", action=request.action)
        actions: dict[str, PlanAction] = {
            "create": self._create,
            "list": self._list,
            "update": self._update,
            "add": self._add,
            "remove": self._remove,
            "clear": self._clear,
        }
        handler = actions.get(request.action)
        if handler is None:
            return f"Error: Unknown action '{request.action}'."
        return await handler(request)

    async def load(self) -> dict[str, Any] | None:
        """Load the current plan, returning none for missing or invalid data."""
        try:
            raw = await self._api.workspace_read(_PLAN_PATH)
            parsed = json.loads(raw)
        except Exception as exc:
            _logger.debug("tool.plan_load_failed", error=str(exc))
            return None
        return parsed if isinstance(parsed, dict) else None

    async def save(self, data: dict[str, Any]) -> None:
        """Persist the current plan as readable JSON."""
        await self._api.workspace_write(
            _PLAN_PATH,
            json.dumps(data, ensure_ascii=False, indent=2),
        )

    async def _create(self, request: _PlanRequest) -> str:
        plan = {
            "title": request.title or "Untitled Plan",
            "tasks": [
                self._new_task(task, task_id=index)
                for index, task in enumerate(request.tasks, start=1)
            ],
        }
        await self.save(plan)
        return f"Plan created.\n{self.format_plan(plan)}"

    async def _list(self, request: _PlanRequest) -> str:
        plan = await self.load()
        if plan is None:
            return "No plan exists. Use action='create' to start one."
        return self.format_plan(plan)

    async def _add(self, request: _PlanRequest) -> str:
        plan = await self.load()
        if plan is None:
            return "No plan exists. Use action='create' to start one."
        current_tasks: list[dict[str, Any]] = plan.get("tasks", [])
        next_id = max((int(task["id"]) for task in current_tasks), default=0) + 1
        for task in request.tasks:
            current_tasks.append(self._new_task(task, task_id=next_id))
            next_id += 1
        plan["tasks"] = current_tasks
        await self.save(plan)
        return f"Tasks added.\n{self.format_plan(plan)}"

    async def _update(self, request: _PlanRequest) -> str:
        plan = await self.load()
        if plan is None:
            return "No plan exists."
        if request.task_id is None:
            return "Error: task_id is required for update."
        if request.status and request.status not in _VALID_STATUSES:
            allowed = ", ".join(sorted(_VALID_STATUSES))
            return (
                f"Error: Invalid status '{request.status}'. Must be one of: {allowed}"
            )
        task = self._find_task(plan, request.task_id)
        if task is None:
            return f"Error: Task {request.task_id} not found."
        if request.status:
            task["status"] = request.status
        if request.detail:
            task["detail"] = request.detail
        await self.save(plan)
        return f"Task {request.task_id} updated.\n{self.format_plan(plan)}"

    async def _remove(self, request: _PlanRequest) -> str:
        plan = await self.load()
        if plan is None:
            return "No plan exists."
        if request.task_id is None:
            return "Error: task_id is required for remove."
        tasks = [
            task for task in plan.get("tasks", []) if task["id"] != request.task_id
        ]
        if len(tasks) == len(plan.get("tasks", [])):
            return f"Error: Task {request.task_id} not found."
        for index, task in enumerate(tasks, start=1):
            task["id"] = index
        plan["tasks"] = tasks
        await self.save(plan)
        return f"Task removed.\n{self.format_plan(plan)}"

    async def _clear(self, request: _PlanRequest) -> str:
        try:
            await self._api.workspace_write(_PLAN_PATH, "")
        except Exception as exc:
            _logger.debug("tool.plan_clear_failed", error=str(exc))
        return "Plan cleared."

    @staticmethod
    def _new_task(task: dict[str, str], *, task_id: int) -> dict[str, Any]:
        return {
            "id": task_id,
            "title": task["title"],
            "status": "pending",
            "detail": task.get("detail", ""),
        }

    @staticmethod
    def _find_task(
        plan: dict[str, Any],
        task_id: int,
    ) -> dict[str, Any] | None:
        return next(
            (task for task in plan.get("tasks", []) if task["id"] == task_id),
            None,
        )

    @staticmethod
    def format_plan(data: dict[str, Any]) -> str:
        """Render a plan for model-facing output."""
        lines = [f"Plan: {data.get('title', 'Untitled')}"]
        tasks = data.get("tasks", [])
        if not tasks:
            lines.append("  (no tasks)")
        markers = {
            "pending": "[ ]",
            "in_progress": "[~]",
            "completed": "[x]",
            "failed": "[!]",
        }
        for task in tasks:
            marker = markers.get(task.get("status", "pending"), "[ ]")
            line = f"  {task['id']}. {marker} {task['title']}"
            if task.get("detail"):
                line += f" — {task['detail']}"
            lines.append(line)
        return "\n".join(lines)
