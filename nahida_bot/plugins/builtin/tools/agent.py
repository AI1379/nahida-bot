"""Subagent orchestration tools for the builtin commands plugin."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from nahida_bot.agent.orchestration.models import SubagentSpec
from nahida_bot.core.context import current_agent_run, current_session
from nahida_bot.core.runtime_settings import REASONING_EFFORTS
from nahida_bot.plugins.tooling import PluginToolDefinition
from nahida_bot_sdk.api import BotAPI


_SPAWN_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task": {
            "type": "string",
            "description": "Concrete delegated task for the subagent.",
        },
        "label": {
            "type": "string",
            "description": "Short display label for the task.",
        },
        "instructions": {
            "type": "string",
            "description": "Temporary task-specific instructions.",
        },
        "context_mode": {
            "type": "string",
            "enum": ["isolated", "summary", "fork"],
            "description": "How much parent context to pass.",
        },
        "provider_id": {
            "type": "string",
            "description": "Optional provider id for the child agent.",
        },
        "model": {
            "type": "string",
            "description": "Optional model or provider/model for the child agent.",
        },
        "reasoning_effort": {
            "type": "string",
            "enum": sorted(REASONING_EFFORTS),
            "description": "Optional reasoning effort override for the child agent.",
        },
        "handoff_summary": {
            "type": "string",
            "description": "Brief parent context summary for summary mode.",
        },
        "timeout_seconds": {
            "type": "integer",
            "description": "Maximum subagent runtime in seconds.",
        },
        "notify": {
            "type": "string",
            "enum": ["done_only", "silent"],
            "description": (
                "Whether to write a completion event to the parent session."
            ),
        },
        "tool_denylist": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Extra tool names to hide from the child.",
        },
        "tool_allowlist": {
            "type": "array",
            "items": {"type": "string"},
            "description": "If set, only these tool names are visible to the child.",
        },
    },
    "required": ["task"],
    "additionalProperties": False,
}
_WAIT_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
        "timeout_seconds": {"type": "integer"},
    },
    "required": ["task_id"],
    "additionalProperties": False,
}
_LIST_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {"limit": {"type": "integer"}},
    "required": [],
    "additionalProperties": False,
}
_STOP_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {"task_id": {"type": "string"}},
    "required": ["task_id"],
    "additionalProperties": False,
}


@dataclass(slots=True, frozen=True)
class _SpawnRequest:
    task: str
    label: str = ""
    instructions: str = ""
    context_mode: str = "isolated"
    provider_id: str = ""
    model: str = ""
    reasoning_effort: str = ""
    handoff_summary: str = ""
    timeout_seconds: int | None = None
    notify: str = "done_only"
    tool_allowlist: tuple[str, ...] = ()
    tool_denylist: tuple[str, ...] = ()

    @classmethod
    def from_arguments(cls, task: str, arguments: dict[str, Any]) -> _SpawnRequest:
        return cls(
            task=task,
            label=str(arguments.get("label", "")),
            instructions=str(arguments.get("instructions", "")),
            context_mode=str(arguments.get("context_mode", "isolated")),
            provider_id=str(arguments.get("provider_id", "")),
            model=str(arguments.get("model", "")),
            reasoning_effort=str(arguments.get("reasoning_effort", "")),
            handoff_summary=str(arguments.get("handoff_summary", "")),
            timeout_seconds=arguments.get("timeout_seconds"),
            notify=str(arguments.get("notify", "done_only")),
            tool_allowlist=tuple(arguments.get("tool_allowlist") or ()),
            tool_denylist=tuple(arguments.get("tool_denylist") or ()),
        )

    def to_spec(self) -> SubagentSpec:
        """Convert normalized tool arguments to the orchestration request."""
        return SubagentSpec(
            task=self.task,
            label=self.label or None,
            instructions=self.instructions or None,
            context_mode=self.context_mode,  # type: ignore[arg-type]
            handoff_summary=self.handoff_summary or None,
            provider_id=self.provider_id or None,
            model=self.model or None,
            reasoning_effort=self.reasoning_effort or None,
            timeout_seconds=self.timeout_seconds,
            tool_allowlist=self.tool_allowlist,
            tool_denylist=self.tool_denylist,
            notify_policy=self.notify,  # type: ignore[arg-type]
        )


class AgentTools:
    """Expose session-owned background-agent lifecycle operations."""

    def __init__(self, api: BotAPI) -> None:
        self._api = api

    @property
    def orchestrator(self) -> Any:
        """Return the optional orchestration service."""
        return getattr(self._api, "orchestration_service", None)

    def definitions(self) -> tuple[PluginToolDefinition, ...]:
        """Return all model-facing subagent tools."""
        return (
            PluginToolDefinition(
                name="agent_spawn",
                description=(
                    "Start a one-off background subagent task in an isolated child "
                    "session."
                ),
                parameters=_SPAWN_PARAMETERS,
                handler=self.spawn,
            ),
            PluginToolDefinition(
                name="agent_wait",
                description=(
                    "Wait for a subagent task result. Timeout does not cancel the task."
                ),
                parameters=_WAIT_PARAMETERS,
                handler=self.wait,
            ),
            # agent_yield was removed because it implied continuation semantics
            # the runtime never implemented. The denylist still pins the stale name.
            PluginToolDefinition(
                name="agent_list",
                description="List subagent tasks created by the current session.",
                parameters=_LIST_PARAMETERS,
                handler=self.list_tasks,
            ),
            PluginToolDefinition(
                name="agent_stop",
                description="Cancel a subagent task created by the current session.",
                parameters=_STOP_PARAMETERS,
                handler=self.stop,
            ),
        )

    async def spawn(self, task: str, **arguments: Any) -> str:
        """Spawn a one-off background subagent."""
        orchestrator = self.orchestrator
        if orchestrator is None:
            return "Error: Agent orchestration service is not available."
        try:
            request = _SpawnRequest.from_arguments(task, arguments)
            background_task = await orchestrator.spawn_subagent(request.to_spec())
        except Exception as exc:
            return f"Error spawning subagent: {exc}"
        return json.dumps(
            {
                "task_id": background_task.task_id,
                "child_session_id": background_task.child_session_id,
                "status": background_task.status.value,
                "title": background_task.title,
            },
            ensure_ascii=False,
        )

    async def wait(self, task_id: str, timeout_seconds: int = 30) -> str:
        """Wait for a task owned by the current requester session."""
        requester_session_id = self.current_requester_session_id()
        if requester_session_id is None:
            return "Error: No active session context."
        orchestrator = self.orchestrator
        if orchestrator is None:
            return "Error: Agent orchestration service is not available."
        task = await orchestrator.wait_for_task(
            task_id,
            timeout_seconds=max(timeout_seconds, 0),
        )
        if task is None or task.requester_session_id != requester_session_id:
            return f"Task {task_id} not found."
        return self.format_background_task(task)

    async def list_tasks(self, limit: int = 20) -> str:
        """List tasks owned by the current requester session."""
        requester_session_id = self.current_requester_session_id()
        if requester_session_id is None:
            return "Error: No active session context."
        orchestrator = self.orchestrator
        if orchestrator is None:
            return "Error: Agent orchestration service is not available."
        tasks = await orchestrator.list_tasks(requester_session_id, limit=max(limit, 1))
        if not tasks:
            return "No subagent tasks for this session."
        return "\n".join(self.format_background_task(task) for task in tasks)

    async def stop(self, task_id: str) -> str:
        """Stop a task owned by the current requester session."""
        requester_session_id = self.current_requester_session_id()
        if requester_session_id is None:
            return "Error: No active session context."
        orchestrator = self.orchestrator
        if orchestrator is None:
            return "Error: Agent orchestration service is not available."
        task = await orchestrator.stop_task(requester_session_id, task_id)
        if task is None:
            return f"Task {task_id} not found or not owned by this session."
        return self.format_background_task(task)

    @staticmethod
    def current_requester_session_id() -> str | None:
        """Resolve ownership to the parent requester during nested agent runs."""
        run_context = current_agent_run.get()
        if run_context is not None:
            return run_context.requester_session_id
        session_context = current_session.get()
        return session_context.session_id if session_context is not None else None

    @staticmethod
    def format_background_task(task: Any) -> str:
        """Render one background task with bounded result and error details."""
        lines = [
            f"{task.task_id}: {task.status.value} — {task.title}",
            f"  child_session: {task.child_session_id or '(none)'}",
        ]
        if task.summary:
            lines.append(f"  summary: {task.summary[:1000]}")
        if task.error:
            lines.append(f"  error: {task.error[:1000]}")
        return "\n".join(lines)
