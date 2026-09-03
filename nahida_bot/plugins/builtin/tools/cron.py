"""Scheduled-task tools and command adapters for the builtin plugin."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from nahida_bot.core.context import current_session
from nahida_bot.plugins.builtin.tools.context import (
    address_from_inbound,
    address_from_session_context,
    job_visible_to_user,
    typed_address_from_session_context,
)
from nahida_bot.plugins.tooling import PluginToolDefinition
from nahida_bot_sdk.api import BotAPI
from nahida_bot_sdk.messaging import InboundMessage


_EMPTY_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}
_JOB_ID_PROPERTY = {
    "type": "string",
    "description": "The job ID returned by cron_create or shown in cron_list.",
}
_CREATE_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "description": "The text prompt to execute when the task fires.",
        },
        "mode": {
            "type": "string",
            "enum": ["once", "interval", "cron"],
            "description": (
                "'once' fires at a specific datetime; 'interval' fires repeatedly; "
                "'cron' uses a 5-field cron expression."
            ),
        },
        "fire_at": {
            "type": "string",
            "description": (
                "ISO 8601 datetime for 'once' mode, e.g. "
                "'2025-06-15T09:00:00'. If no timezone is given, UTC is assumed."
            ),
        },
        "interval_seconds": {
            "type": "integer",
            "description": ("Seconds between fires for 'interval' mode. Minimum 60."),
        },
        "cron_expression": {
            "type": "string",
            "description": (
                "5-field cron expression for 'cron' mode, e.g. '0 9 * * 1-5'."
            ),
        },
        "max_runs": {
            "type": "integer",
            "description": (
                "Max number of fires for interval or cron mode. Omit for infinite."
            ),
        },
        "session_mode": {
            "type": "string",
            "enum": ["main", "isolated", "fresh"],
            "description": (
                "'main' (default) uses the chat session. 'isolated' reuses one "
                "private session per cron job. 'fresh' creates a new session for "
                "each fire."
            ),
        },
        "executor_type": {
            "type": "string",
            "enum": ["agent", "script_then_agent"],
            "description": (
                "'agent' (default) runs the prompt through the Agent. "
                "'script_then_agent' runs an external command first and only "
                "uses the Agent when the command fails, times out, or cannot start."
            ),
        },
        "script_command": {
            "type": "string",
            "description": (
                "Shell command for executor_type='script_then_agent'. Use an "
                "explicit interpreter path when the script needs its own venv."
            ),
        },
        "script_working_dir": {
            "type": "string",
            "description": "Optional working directory for the external command.",
        },
        "script_timeout_seconds": {
            "type": "integer",
            "description": "External command timeout in seconds. Default 30.",
        },
    },
    "required": ["prompt", "mode"],
    "additionalProperties": False,
}
_UPDATE_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "job_id": _JOB_ID_PROPERTY,
        "prompt": {
            "type": "string",
            "description": "Replacement prompt to execute when the task fires.",
        },
        "mode": {
            "type": "string",
            "enum": ["once", "interval", "cron"],
            "description": "Switch the task to one-shot, interval, or cron mode.",
        },
        "fire_at": {
            "type": "string",
            "description": (
                "ISO 8601 datetime for one-shot mode. If no timezone is given, "
                "UTC is assumed."
            ),
        },
        "interval_seconds": {
            "type": "integer",
            "description": "Seconds between fires for interval mode. Minimum 60.",
        },
        "cron_expression": {
            "type": "string",
            "description": (
                "5-field cron expression for cron mode, e.g. '0 9 * * 1-5'."
            ),
        },
        "max_runs": {
            "type": "integer",
            "description": (
                "Max number of successful fires for interval or cron mode."
            ),
        },
        "executor_type": {
            "type": "string",
            "enum": ["agent", "script_then_agent"],
            "description": "Switch between the Agent and script-first executor.",
        },
        "script_command": {
            "type": "string",
            "description": "Replacement external command for a script-first task.",
        },
        "script_working_dir": {
            "type": "string",
            "description": "Replacement working directory for the external command.",
        },
        "script_timeout_seconds": {
            "type": "integer",
            "description": "Replacement external command timeout in seconds.",
        },
    },
    "required": ["job_id"],
    "additionalProperties": False,
}


@dataclass(slots=True, frozen=True)
class _CreateRequest:
    prompt: str
    mode: str
    fire_at: str | None = None
    interval_seconds: int | None = None
    cron_expression: str | None = None
    max_runs: int | None = None
    session_mode: str = "main"
    executor_type: str = "agent"
    script_command: str = ""
    script_working_dir: str = ""
    script_timeout_seconds: int = 30

    @classmethod
    def from_arguments(
        cls,
        prompt: str,
        mode: str,
        arguments: dict[str, Any],
    ) -> _CreateRequest:
        return cls(
            prompt=prompt,
            mode=mode,
            fire_at=arguments.get("fire_at"),
            interval_seconds=arguments.get("interval_seconds"),
            cron_expression=arguments.get("cron_expression"),
            max_runs=arguments.get("max_runs"),
            session_mode=str(arguments.get("session_mode", "main")),
            executor_type=str(arguments.get("executor_type", "agent")),
            script_command=str(arguments.get("script_command", "")),
            script_working_dir=str(arguments.get("script_working_dir", "")),
            script_timeout_seconds=int(arguments.get("script_timeout_seconds", 30)),
        )


@dataclass(slots=True, frozen=True)
class _UpdateRequest:
    job_id: str
    prompt: str | None = None
    mode: str | None = None
    fire_at: str | None = None
    interval_seconds: int | None = None
    cron_expression: str | None = None
    max_runs: int | None = None
    executor_type: str | None = None
    script_command: str | None = None
    script_working_dir: str | None = None
    script_timeout_seconds: int | None = None

    @classmethod
    def from_arguments(cls, job_id: str, arguments: dict[str, Any]) -> _UpdateRequest:
        return cls(
            job_id=job_id,
            prompt=arguments.get("prompt"),
            mode=arguments.get("mode"),
            fire_at=arguments.get("fire_at"),
            interval_seconds=arguments.get("interval_seconds"),
            cron_expression=arguments.get("cron_expression"),
            max_runs=arguments.get("max_runs"),
            executor_type=arguments.get("executor_type"),
            script_command=arguments.get("script_command"),
            script_working_dir=arguments.get("script_working_dir"),
            script_timeout_seconds=arguments.get("script_timeout_seconds"),
        )


class CronTools:
    """Own cron schemas, validation, authorization, and response formatting."""

    def __init__(self, api: BotAPI) -> None:
        self._api = api

    @property
    def scheduler(self) -> Any:
        """Return the scheduler service attached to the plugin API."""
        return self._api.scheduler_service

    def definitions(self) -> tuple[PluginToolDefinition, ...]:
        """Return all model-facing scheduled-task tools."""
        return (
            PluginToolDefinition(
                name="cron_create",
                description=(
                    "Create a scheduled task that runs a prompt once, repeatedly "
                    "at a fixed interval, or by a 5-field cron expression."
                ),
                parameters=_CREATE_PARAMETERS,
                handler=self.create,
            ),
            PluginToolDefinition(
                name="cron_list",
                description="List all active scheduled tasks for the current chat.",
                parameters=_EMPTY_PARAMETERS,
                handler=self.list_active,
            ),
            PluginToolDefinition(
                name="cron_cancel",
                description="Cancel a scheduled task by its job ID.",
                parameters=self._job_parameters(),
                handler=self.cancel,
            ),
            PluginToolDefinition(
                name="cron_update",
                description=(
                    "Update an active scheduled task's prompt, schedule, or max "
                    "run count."
                ),
                parameters=_UPDATE_PARAMETERS,
                handler=self.update,
            ),
            PluginToolDefinition(
                name="cron_delete",
                description="Permanently delete a scheduled task by its job ID.",
                parameters=self._job_parameters(),
                handler=self.delete,
            ),
        )

    async def create(self, prompt: str, mode: str, **arguments: Any) -> str:
        """Create one scheduled task for the current typed chat."""
        context = current_session.get()
        if context is None:
            return "Error: No active session context."
        scheduler = self.scheduler
        if scheduler is None:
            return "Error: Scheduler is not available."

        request = _CreateRequest.from_arguments(prompt, mode, arguments)
        validation_error, normalized_fire_at = self._validate_create(request)
        if validation_error:
            return validation_error
        authorization_error = self._script_executor_authorization_error(
            request.executor_type
        )
        if authorization_error:
            return authorization_error
        address = typed_address_from_session_context(context)
        if address is None:
            return "Error: Current chat does not have a typed delivery target."

        try:
            job = await scheduler.create_job(
                address=address,
                prompt=request.prompt,
                mode=request.mode,
                fire_at=normalized_fire_at if request.mode == "once" else None,
                interval_seconds=request.interval_seconds,
                cron_expression=request.cron_expression,
                max_runs=request.max_runs,
                workspace_id=context.workspace_id,
                session_mode=request.session_mode,
                executor_type=request.executor_type,
                script_command=request.script_command,
                script_working_dir=request.script_working_dir,
                script_timeout_seconds=request.script_timeout_seconds,
                created_by_user_id=context.user_id,
                created_from_session_id=context.session_id,
                created_from_chat_address=address.chat_key,
                # The initiator may be a conversation-joiner anchor in groups;
                # cron ownership for auto-join scenarios still needs policy work.
                sender_account_key=context.sender_account_key,
            )
        except Exception as exc:
            return f"Error creating scheduled task: {exc}"
        return self._format_created(job, request)

    async def list_active(self) -> str:
        """List active jobs visible to the current user in the current chat."""
        context = current_session.get()
        if context is None:
            return "Error: No active session context."
        scheduler = self.scheduler
        if scheduler is None:
            return "Error: Scheduler is not available."

        address = address_from_session_context(context)
        jobs = [
            job
            for job in await scheduler.list_jobs(address)
            if job_visible_to_user(job, address, context.user_id)
        ]
        if not jobs:
            return "No active scheduled tasks for this chat."
        return self._format_active_jobs(jobs)

    async def cancel(self, job_id: str) -> str:
        """Cancel a current-chat job without deleting it."""
        resolved = await self._resolve_tool_job(job_id)
        if isinstance(resolved, str):
            return resolved
        scheduler, _job = resolved
        cancelled = await scheduler.cancel_job(job_id)
        if cancelled:
            return f"Cancelled task {job_id}."
        return f"Task {job_id} is already inactive or completed."

    async def update(self, job_id: str, **arguments: Any) -> str:
        """Update an active current-chat job."""
        resolved = await self._resolve_tool_job(job_id)
        if isinstance(resolved, str):
            return resolved
        scheduler, _job = resolved
        request = _UpdateRequest.from_arguments(job_id, arguments)
        validation_error = self._validate_update(request)
        if validation_error:
            return validation_error
        effective_executor_type = request.executor_type or str(
            getattr(_job, "executor_type", "agent")
        )
        authorization_error = self._script_executor_authorization_error(
            effective_executor_type
        )
        if authorization_error:
            return authorization_error

        try:
            updated = await scheduler.update_job(
                job_id,
                prompt=request.prompt,
                mode=request.mode,
                fire_at=request.fire_at,
                interval_seconds=request.interval_seconds,
                cron_expression=request.cron_expression,
                max_runs=request.max_runs,
                executor_type=request.executor_type,
                script_command=request.script_command,
                script_working_dir=request.script_working_dir,
                script_timeout_seconds=request.script_timeout_seconds,
            )
        except Exception as exc:
            return f"Error updating scheduled task: {exc}"
        return self._format_updated(updated)

    async def delete(self, job_id: str) -> str:
        """Permanently delete a current-chat job."""
        resolved = await self._resolve_tool_job(job_id)
        if isinstance(resolved, str):
            return resolved
        scheduler, _job = resolved
        deleted = await scheduler.delete_job(job_id)
        if deleted:
            return f"Deleted task {job_id}."
        return f"Task {job_id} was already deleted."

    async def list_for_inbound(self, inbound: InboundMessage) -> str:
        """Implement the concise /cron list response."""
        scheduler = self.scheduler
        if scheduler is None:
            return "Scheduler is not available."
        address = address_from_inbound(inbound)
        jobs = [
            job
            for job in await scheduler.list_jobs(address)
            if job_visible_to_user(job, address, inbound.user_id)
        ]
        if not jobs:
            return "No scheduled tasks for this chat."
        return self._format_command_jobs(jobs)

    async def cancel_for_inbound(
        self,
        job_id: str,
        inbound: InboundMessage,
    ) -> str:
        """Implement /cron cancel with command-compatible errors."""
        if not job_id:
            return "Usage: /cron cancel <job_id>"
        resolved = await self._resolve_inbound_job(job_id, inbound)
        if isinstance(resolved, str):
            return resolved
        scheduler, _job = resolved
        cancelled = await scheduler.cancel_job(job_id)
        if cancelled:
            return f"Cancelled task {job_id}."
        return f"Task {job_id} is already inactive."

    async def delete_for_inbound(
        self,
        job_id: str,
        inbound: InboundMessage,
    ) -> str:
        """Implement /cron delete with command-compatible errors."""
        if not job_id:
            return "Usage: /cron delete <job_id>"
        resolved = await self._resolve_inbound_job(job_id, inbound)
        if isinstance(resolved, str):
            return resolved
        scheduler, _job = resolved
        deleted = await scheduler.delete_job(job_id)
        if deleted:
            return f"Deleted task {job_id}."
        return f"Task {job_id} was already deleted."

    async def _resolve_tool_job(self, job_id: str) -> tuple[Any, Any] | str:
        context = current_session.get()
        if context is None:
            return "Error: No active session context."
        scheduler = self.scheduler
        if scheduler is None:
            return "Error: Scheduler is not available."
        job = await scheduler.get_job(job_id)
        if job is None:
            return f"Error: Job '{job_id}' not found."
        address = typed_address_from_session_context(context)
        if address is None or not job_visible_to_user(
            job,
            address,
            context.user_id,
        ):
            return f"Error: Job '{job_id}' does not belong to this chat."
        return scheduler, job

    async def _resolve_inbound_job(
        self,
        job_id: str,
        inbound: InboundMessage,
    ) -> tuple[Any, Any] | str:
        scheduler = self.scheduler
        if scheduler is None:
            return "Scheduler is not available."
        job = await scheduler.get_job(job_id)
        address = address_from_inbound(inbound)
        if (
            job is None
            or not address.is_typed
            or not job_visible_to_user(job, address, inbound.user_id)
        ):
            return f"Task '{job_id}' not found."
        return scheduler, job

    @staticmethod
    def _validate_create(request: _CreateRequest) -> tuple[str | None, str | None]:
        if request.executor_type not in {"agent", "script_then_agent"}:
            return (
                "Error: 'executor_type' must be 'agent' or 'script_then_agent'.",
                None,
            )
        if (
            request.executor_type == "script_then_agent"
            and not request.script_command.strip()
        ):
            return (
                "Error: 'script_command' is required for "
                "executor_type='script_then_agent'.",
                None,
            )
        if request.script_timeout_seconds <= 0:
            return "Error: 'script_timeout_seconds' must be > 0.", None
        if request.mode == "once":
            if not request.fire_at:
                return "Error: 'fire_at' is required for mode='once'.", None
            try:
                parsed = datetime.fromisoformat(request.fire_at)
            except ValueError:
                return f"Error: Invalid datetime format: {request.fire_at}", None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return None, parsed.astimezone(UTC).isoformat()
        if request.mode == "interval":
            if not request.interval_seconds or request.interval_seconds < 60:
                return (
                    "Error: 'interval_seconds' must be >= 60 for mode='interval'.",
                    None,
                )
            if request.max_runs is not None and request.max_runs <= 0:
                return "Error: 'max_runs' must be > 0 when provided.", None
            return None, request.fire_at
        if request.mode == "cron":
            if not request.cron_expression:
                return "Error: 'cron_expression' is required for mode='cron'.", None
            return None, request.fire_at
        return (
            f"Error: Invalid mode '{request.mode}'. Use 'once', 'interval', or 'cron'.",
            None,
        )

    @staticmethod
    def _validate_update(request: _UpdateRequest) -> str | None:
        if request.mode is not None and request.mode not in {
            "once",
            "interval",
            "cron",
        }:
            return (
                f"Error: Invalid mode '{request.mode}'. "
                "Use 'once', 'interval', or 'cron'."
            )
        if request.interval_seconds is not None and request.interval_seconds < 60:
            return "Error: 'interval_seconds' must be >= 60 for mode='interval'."
        if request.max_runs is not None and request.max_runs <= 0:
            return "Error: 'max_runs' must be > 0 when provided."
        if request.executor_type is not None and request.executor_type not in {
            "agent",
            "script_then_agent",
        }:
            return "Error: 'executor_type' must be 'agent' or 'script_then_agent'."
        if (
            request.script_timeout_seconds is not None
            and request.script_timeout_seconds <= 0
        ):
            return "Error: 'script_timeout_seconds' must be > 0."
        return None

    def _script_executor_authorization_error(self, executor_type: str) -> str | None:
        """Require an admin sender when a cron job will run a local shell."""
        if executor_type != "script_then_agent":
            return None
        context = current_session.get()
        if context is None:
            # WebAPI mutations already pass through the authenticated Gateway
            # boundary and do not carry a channel sender context.
            return None
        event_bus = getattr(self._api, "_event_bus", None)
        app = getattr(getattr(event_bus, "context", None), "app", None)
        gate = getattr(app, "_authorization_gate", None)
        if gate is None or not getattr(gate, "enabled", False):
            return None
        if gate.is_admin(context.actor_account_key):
            return None
        return "Error: script_then_agent requires an admin sender."

    @staticmethod
    def _format_created(job: Any, request: _CreateRequest) -> str:
        lines = [f"Scheduled task created (id: {job.job_id})"]
        if request.mode == "once":
            lines.append(f"  Mode: once at {job.next_fire_at}")
        elif request.mode == "cron":
            lines.append(f"  Mode: cron ({request.cron_expression})")
            lines.append(f"  Max runs: {request.max_runs or 'infinite'}")
        else:
            lines.append(f"  Mode: every {request.interval_seconds}s")
            lines.append(f"  Max runs: {request.max_runs or 'infinite'}")
        lines.append(f"  Next fire: {job.next_fire_at}")
        lines.append(f"  Session: {job.session_mode}")
        lines.append(f"  Executor: {job.executor_type}")
        if job.executor_type == "script_then_agent":
            command = job.script_command
            lines.append(
                f"  Script: {command[:100]}{'...' if len(command) > 100 else ''}"
            )
        prompt = request.prompt
        lines.append(f"  Prompt: {prompt[:100]}{'...' if len(prompt) > 100 else ''}")
        return "\n".join(lines)

    @classmethod
    def _format_active_jobs(cls, jobs: list[Any]) -> str:
        lines = [f"Active scheduled tasks ({len(jobs)}):"]
        for job in jobs:
            preview = job.prompt[:60] + ("..." if len(job.prompt) > 60 else "")
            lines.append(
                f"  {job.job_id}: "
                f"[{job.mode}/{job.session_mode}/{job.executor_type}] "
                f"{cls._schedule_description(job)} — {preview}"
            )
            lines.append(f"    runs: {job.run_count}, next: {job.next_fire_at}")
            if job.failure_count:
                lines.append(
                    f"    failures: {job.failure_count}, last error: {job.last_error}"
                )
        return "\n".join(lines)

    @classmethod
    def _format_updated(cls, job: Any) -> str:
        lines = [f"Updated task {job.job_id}."]
        if job.mode == "once":
            lines.append(f"  Mode: once at {job.next_fire_at}")
        elif job.mode == "cron":
            lines.append(f"  Mode: cron ({job.cron_expression})")
            lines.append(f"  Max runs: {job.max_runs or 'infinite'}")
        else:
            lines.append(f"  Mode: every {job.interval_seconds}s")
            lines.append(f"  Max runs: {job.max_runs or 'infinite'}")
        lines.append(f"  Next fire: {job.next_fire_at}")
        lines.append(f"  Executor: {job.executor_type}")
        prompt = job.prompt
        lines.append(f"  Prompt: {prompt[:100]}{'...' if len(prompt) > 100 else ''}")
        return "\n".join(lines)

    @staticmethod
    def _format_command_jobs(jobs: list[Any]) -> str:
        lines: list[str] = []
        for job in jobs:
            status = "active" if job.is_active else "inactive"
            next_at = f", next: {job.next_fire_at}" if job.is_active else ""
            lines.append(
                f"  {job.job_id}  [{job.mode}/{job.executor_type}] {status}  "
                f"runs: {job.run_count}{next_at}"
            )
            preview = job.prompt[:80] + ("..." if len(job.prompt) > 80 else "")
            lines.append(f"    {preview}")
        return "\n".join(lines)

    @staticmethod
    def _schedule_description(job: Any) -> str:
        if job.mode == "once":
            return f"once at {job.next_fire_at}"
        if job.mode == "cron":
            return f"cron ({job.cron_expression})"
        return f"every {job.interval_seconds}s"

    @staticmethod
    def _job_parameters() -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"job_id": _JOB_ID_PROPERTY},
            "required": ["job_id"],
            "additionalProperties": False,
        }
