"""High-level local agent orchestration service."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import structlog

from nahida_bot.agent.memory.models import ConversationTurn
from nahida_bot.agent.orchestration.executors import AgentRunExecutor
from nahida_bot.agent.orchestration.models import (
    AgentRun,
    AgentRunKind,
    AgentRunPayload,
    AgentRunStatus,
    BackgroundTask,
    SubagentSpec,
    TaskRuntime,
    utc_now,
)
from nahida_bot.agent.orchestration.policy import OrchestrationPolicy
from nahida_bot.agent.orchestration.registry import AgentRegistry
from nahida_bot.agent.orchestration.task_store import BackgroundTaskStore
from nahida_bot.core.context import current_agent_run, current_session

logger = structlog.get_logger(__name__)

_DEFAULT_CHILD_TOOL_DENYLIST = frozenset(
    {
        "agent_spawn",
        "agent_yield",
        "agent_wait",
        "agent_stop",
        "sessions_send",
    }
)


@dataclass(slots=True, frozen=True)
class OrchestrationConfig:
    """Runtime limits for the local orchestration MVP."""

    max_child_agents_per_run: int = 5
    subagent_timeout_seconds: int = 900
    subagent_concurrency: int = 4
    system_prompt: str = "You are a focused subagent. Complete the delegated task and return a concise result summary."


class AgentOrchestrator:
    """Coordinates local child agent runs and background task state."""

    def __init__(
        self,
        *,
        executor: AgentRunExecutor,
        task_store: BackgroundTaskStore,
        memory_store: Any | None = None,
        policy: OrchestrationPolicy | None = None,
        config: OrchestrationConfig | None = None,
    ) -> None:
        self._executor = executor
        self._task_store = task_store
        self._memory = memory_store
        self._config = config or OrchestrationConfig()
        self._policy = policy or OrchestrationPolicy(
            max_child_agents_per_run=self._config.max_child_agents_per_run
        )
        self._registry = AgentRegistry()
        self._subagent_sem = asyncio.Semaphore(self._config.subagent_concurrency)

    async def spawn_subagent(self, spec: SubagentSpec) -> BackgroundTask:
        session_ctx = current_session.get()
        if session_ctx is None:
            raise RuntimeError("No active session context for agent_spawn.")

        run_ctx = current_agent_run.get()
        depth = run_ctx.depth if run_ctx is not None else 0
        requester_session_id = (
            run_ctx.requester_session_id
            if run_ctx is not None
            else session_ctx.session_id
        )
        active_count = self._registry.active_child_count(requester_session_id)
        await self._policy.can_spawn(
            requester_session_id,
            spec,
            active_child_count=active_count,
            depth=depth,
        )

        task_id = f"task_{uuid4().hex[:12]}"
        run_id = f"run_{uuid4().hex[:12]}"
        child_session_id = f"{requester_session_id}:subagent:{task_id}"
        title = spec.label or spec.task.strip().splitlines()[0][:80] or task_id

        task = BackgroundTask(
            task_id=task_id,
            runtime=TaskRuntime.SUBAGENT,
            status=AgentRunStatus.QUEUED,
            requester_session_id=requester_session_id,
            child_session_id=child_session_id,
            parent_task_id=run_ctx.task_id if run_ctx else None,
            title=title,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        await self._task_store.create(task)

        run = AgentRun(
            run_id=run_id,
            kind=AgentRunKind.SUBAGENT,
            session_id=child_session_id,
            parent_run_id=run_ctx.run_id if run_ctx else None,
            requester_session_id=requester_session_id,
            task_id=task_id,
            depth=1,
        )
        self._registry.register(run)

        payload = AgentRunPayload(
            user_message=self._build_child_user_message(spec),
            system_prompt=self._build_child_system_prompt(spec),
            requester_session_id=requester_session_id,
            workspace_id=session_ctx.workspace_id,
            model=spec.model,
            tool_filter=frozenset(spec.tool_denylist) | _DEFAULT_CHILD_TOOL_DENYLIST,
            timeout_seconds=spec.timeout_seconds
            or self._config.subagent_timeout_seconds,
        )
        run.asyncio_task = asyncio.create_task(self._run_subagent(run, payload, spec))
        logger.info(
            "subagent.spawned",
            task_id=task_id,
            run_id=run_id,
            requester_session_id=requester_session_id,
            child_session_id=child_session_id,
        )
        return task

    async def wait_for_task(
        self, task_id: str, *, timeout_seconds: float | None = None
    ) -> BackgroundTask | None:
        run = self._registry.get_by_task(task_id)
        if run is not None and run.asyncio_task is not None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(run.asyncio_task),
                    timeout=timeout_seconds,
                )
            except TimeoutError:
                return await self._task_store.get(task_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                # The runner task records failure state in _run_subagent.
                pass
        return await self._task_store.get(task_id)

    async def list_tasks(
        self, requester_session_id: str, *, limit: int = 20
    ) -> list[BackgroundTask]:
        return await self._task_store.list_for_session(
            requester_session_id, limit=limit
        )

    async def stop_task(
        self, requester_session_id: str, task_id: str
    ) -> BackgroundTask | None:
        task = await self._task_store.get(task_id)
        if task is None or task.requester_session_id != requester_session_id:
            return None

        run = self._registry.get_by_task(task_id)
        if (
            run is not None
            and run.asyncio_task is not None
            and not run.asyncio_task.done()
        ):
            run.asyncio_task.cancel()
        await self._task_store.update_status(
            task_id,
            AgentRunStatus.CANCELLED,
            error="Cancelled by requester.",
            terminal=True,
        )
        if run is not None:
            self._registry.set_status(
                run.run_id,
                AgentRunStatus.CANCELLED,
                error="Cancelled by requester.",
            )
        return await self._task_store.get(task_id)

    async def _run_subagent(
        self, run: AgentRun, payload: AgentRunPayload, spec: SubagentSpec
    ) -> object:
        async with self._subagent_sem:
            run.status = AgentRunStatus.RUNNING
            run.started_at = utc_now()
            await self._task_store.update_status(
                run.task_id or "", AgentRunStatus.RUNNING
            )
            try:
                await self._prepare_child_session(run, payload, spec)
                result = await self._executor.run(run, payload)
                summary = result.final_response.strip()
                run.status = AgentRunStatus.SUCCEEDED
                run.summary = summary
                run.ended_at = utc_now()
                await self._task_store.update_status(
                    run.task_id or "",
                    AgentRunStatus.SUCCEEDED,
                    summary=summary,
                    terminal=True,
                )
                if spec.notify_policy != "silent":
                    await self._deliver_completion(
                        run, AgentRunStatus.SUCCEEDED, summary, ""
                    )
                return result
            except asyncio.CancelledError:
                run.status = AgentRunStatus.CANCELLED
                run.error = "Cancelled."
                run.ended_at = utc_now()
                await self._task_store.update_status(
                    run.task_id or "",
                    AgentRunStatus.CANCELLED,
                    error="Cancelled.",
                    terminal=True,
                )
                if spec.notify_policy != "silent":
                    await self._deliver_completion(
                        run, AgentRunStatus.CANCELLED, "", "Cancelled."
                    )
                raise
            except TimeoutError:
                error = "Subagent timed out."
                run.status = AgentRunStatus.TIMED_OUT
                run.error = error
                run.ended_at = utc_now()
                await self._task_store.update_status(
                    run.task_id or "",
                    AgentRunStatus.TIMED_OUT,
                    error=error,
                    terminal=True,
                )
                if spec.notify_policy != "silent":
                    await self._deliver_completion(
                        run, AgentRunStatus.TIMED_OUT, "", error
                    )
                return None
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                run.status = AgentRunStatus.FAILED
                run.error = error
                run.ended_at = utc_now()
                await self._task_store.update_status(
                    run.task_id or "",
                    AgentRunStatus.FAILED,
                    error=error,
                    terminal=True,
                )
                if spec.notify_policy != "silent":
                    await self._deliver_completion(
                        run, AgentRunStatus.FAILED, "", error
                    )
                logger.exception("subagent.failed", task_id=run.task_id)
                return None
            finally:
                self._registry.unregister(run.run_id)

    def _build_child_system_prompt(self, spec: SubagentSpec) -> str:
        parts = [self._config.system_prompt]
        if spec.instructions:
            parts.append("Task-specific instructions:\n" + spec.instructions)
        parts.append("Do not spawn subagents. Return only the useful result summary.")
        return "\n\n".join(parts)

    async def _prepare_child_session(
        self,
        run: AgentRun,
        payload: AgentRunPayload,
        spec: SubagentSpec,
    ) -> None:
        if self._memory is None:
            return
        await self._memory.ensure_session(
            run.session_id, workspace_id=payload.workspace_id
        )
        meta: dict[str, str] = {
            "requester_session_id": payload.requester_session_id,
            "parent_run_id": run.parent_run_id or "",
            "task_id": run.task_id or "",
            "run_kind": run.kind.value,
        }
        if spec.provider_id:
            meta["provider_id"] = spec.provider_id
        if spec.model:
            meta["model"] = spec.model
        await self._memory.update_session_meta(run.session_id, meta)

    @staticmethod
    def _build_child_user_message(spec: SubagentSpec) -> str:
        parts = ["Delegated task:\n" + spec.task]
        if spec.handoff_summary:
            parts.append("Parent context summary:\n" + spec.handoff_summary)
        parts.append("Complete the task independently and report the result.")
        return "\n\n".join(parts)

    async def _deliver_completion(
        self,
        run: AgentRun,
        status: AgentRunStatus,
        summary: str,
        error: str,
    ) -> None:
        if self._memory is None or not run.requester_session_id:
            return
        content = f"Subagent task {run.task_id} completed with status {status.value}."
        if summary:
            content += f"\nSummary:\n{summary}"
        if error:
            content += f"\nError:\n{error}"
        await self._memory.append_turn(
            run.requester_session_id,
            ConversationTurn(
                role="system",
                content=content,
                source="subagent_completed",
                metadata={
                    "event_type": "subagent_completed",
                    "task_id": run.task_id,
                    "child_session_id": run.session_id,
                    "status": status.value,
                    "summary": summary,
                    "error": error,
                },
            ),
        )
