"""High-level local agent orchestration service."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

import structlog

from nahida_bot.agent.loop import AgentRunResult
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
from nahida_bot.core.context import SessionContext, current_agent_run, current_session
from nahida_bot.core.runtime_settings import (
    RUNTIME_META_KEY,
    merge_runtime_meta,
    runtime_meta_from_session_meta,
)

logger = structlog.get_logger(__name__)


def _delivery_target_from_session(
    session_ctx: SessionContext,
) -> dict[str, str] | None:
    """Build a stable channel delivery target from the parent session.

    Issue #41: the completion notification must be able to reach the channel
    the user actually spoke to. We snapshot ``platform`` (channel) and
    ``chat_id`` (target) at spawn time, because by the time the subagent
    finishes the synthetic child session no longer carries the original
    channel. Returns ``None`` for synthetic/internal sessions (no real
    channel to deliver to).
    """
    platform = (session_ctx.platform or "").strip()
    chat_id = (session_ctx.chat_id or "").strip()
    # ``platform="agent"`` marks a synthetic/internal session (e.g. a nested
    # orchestrator run or a cron fire with no chat). There is no real channel
    # to deliver to in that case — completion stays in the parent session
    # memory only.
    if not platform or platform == "agent" or not chat_id:
        return None
    address = session_ctx.chat_address
    if address is not None and address.is_typed:
        return {
            "channel": address.channel,
            "target": address.target_id,
            "chat_address": address.chat_key,
        }
    return {"channel": platform, "target": chat_id}


class CompletionDeliverer(Protocol):
    """Delivers a subagent completion notification to the originating channel.

    Issue #41: subagent results must reach the user, not just the parent
    session memory. Implementations look up the channel via the delivery
    target captured at spawn time and send a concise notification. Returns
    True only when the notification was actually dispatched, so the
    orchestrator can confirm the matching delivery claim.
    """

    async def deliver(
        self,
        *,
        task: BackgroundTask,
        status: AgentRunStatus,
        summary: str,
        error: str,
    ) -> bool: ...


@dataclass(slots=True, frozen=True)
class OrchestrationConfig:
    """Runtime limits for the local orchestration MVP."""

    max_child_agents_per_run: int = 5
    subagent_timeout_seconds: int = 900
    subagent_concurrency: int = 4
    delivery_claim_ttl_seconds: int = 300
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
        completion_deliverer: CompletionDeliverer | None = None,
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
        # Issue #41: optional channel delivery hook. When absent (tests,
        # headless runs), completion only writes to the parent session memory.
        self._deliverer = completion_deliverer

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
            # Issue #41: capture a stable delivery target at spawn time so the
            # completion notification can reach the channel the user actually
            # spoke to, rather than being inferred from the synthetic child
            # session at completion time (where the original channel is gone).
            delivery_target=_delivery_target_from_session(session_ctx),
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

        # Issue #43: the policy is the single source of truth for child tool
        # filtering. The system denylist always wins; any tool the parent
        # lists in ``tool_allowlist`` that is also denied is dropped, so the
        # parent cannot widen the child's capabilities beyond the system
        # baseline.
        effective_allowlist, effective_denylist = (
            self._policy.compute_child_tool_filter(spec)
        )
        logger.info(
            "subagent.tool_profile",
            task_id=task_id,
            system_denylist_sorted=sorted(self._policy.system_tool_denylist),
            effective_allowlist_sorted=sorted(effective_allowlist),
            spec_denylist_sorted=sorted(spec.tool_denylist),
            requested_allowlist_sorted=sorted(spec.tool_allowlist),
        )

        payload = AgentRunPayload(
            user_message=self._build_child_user_message(spec),
            system_prompt=self._build_child_system_prompt(spec),
            requester_session_id=requester_session_id,
            workspace_id=session_ctx.workspace_id,
            provider_id=spec.provider_id,
            model=spec.model,
            reasoning_effort=spec.reasoning_effort,
            tool_allowlist=(effective_allowlist if spec.tool_allowlist else None),
            tool_filter=effective_denylist,
            timeout_seconds=spec.timeout_seconds
            or self._config.subagent_timeout_seconds,
            # Identity delegation (issue #39): inherit the parent sender's
            # auditable account key. This never escalates capability — the
            # child must still clear the AuthorizationGate for privileged
            # tools — but it stops privileged tools from being rejected as
            # "unknown sender" purely because the child session's
            # platform is synthetic.
            sender_account_key=session_ctx.sender_account_key,
            # Preserve the original channel address so completion delivery
            # targets the chat the user actually spoke to (issue #41).
            chat_address=session_ctx.chat_address,
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
        # TODO(subagent-observability): Expose a non-blocking read/subscribe API
        # for live subagent output instead of only polling the final task ledger.
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
                # TODO(subagent-observability): Route executor stream events into
                # the task event stream so WebUI/API callers can inspect live
                # subagent reasoning/output/tool progress while the task is running.
                result = await self._executor.run(run, payload)
                status, summary, error = self._map_run_result_to_task(result)
                self._apply_terminal(
                    run,
                    status=status,
                    summary=summary,
                    error=error,
                    terminal_state=result.terminal_state,
                    terminal_reason=result.terminal_reason,
                )
                await self._task_store.update_status(
                    run.task_id or "",
                    status,
                    summary=summary,
                    error=error,
                    terminal=True,
                    terminal_state=result.terminal_state,
                    terminal_reason=result.terminal_reason,
                )
                if spec.notify_policy != "silent":
                    await self._deliver_completion(run, status, summary, error)
                return result
            except asyncio.CancelledError:
                error = "Cancelled."
                self._apply_terminal(
                    run,
                    status=AgentRunStatus.CANCELLED,
                    summary="",
                    error=error,
                    terminal_state="cancelled",
                    terminal_reason="cancelled",
                )
                await self._task_store.update_status(
                    run.task_id or "",
                    AgentRunStatus.CANCELLED,
                    error=error,
                    terminal=True,
                    terminal_state="cancelled",
                    terminal_reason="cancelled",
                )
                if spec.notify_policy != "silent":
                    await self._deliver_completion(
                        run, AgentRunStatus.CANCELLED, "", error
                    )
                raise
            except TimeoutError:
                error = "Subagent timed out."
                self._apply_terminal(
                    run,
                    status=AgentRunStatus.TIMED_OUT,
                    summary="",
                    error=error,
                    terminal_state="failed",
                    terminal_reason="timeout",
                )
                await self._task_store.update_status(
                    run.task_id or "",
                    AgentRunStatus.TIMED_OUT,
                    error=error,
                    terminal=True,
                    terminal_state="failed",
                    terminal_reason="timeout",
                )
                if spec.notify_policy != "silent":
                    await self._deliver_completion(
                        run, AgentRunStatus.TIMED_OUT, "", error
                    )
                return None
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                self._apply_terminal(
                    run,
                    status=AgentRunStatus.FAILED,
                    summary="",
                    error=error,
                    terminal_state="failed",
                    terminal_reason=type(exc).__name__,
                )
                await self._task_store.update_status(
                    run.task_id or "",
                    AgentRunStatus.FAILED,
                    error=error,
                    terminal=True,
                    terminal_state="failed",
                    terminal_reason=type(exc).__name__,
                )
                if spec.notify_policy != "silent":
                    await self._deliver_completion(
                        run, AgentRunStatus.FAILED, "", error
                    )
                logger.exception("subagent.failed", task_id=run.task_id)
                return None
            finally:
                self._registry.unregister(run.run_id)

    @staticmethod
    def _map_run_result_to_task(
        result: AgentRunResult,
    ) -> tuple[AgentRunStatus, str, str]:
        """Map the loop's trusted terminal state to a task-ledger status.

        Per issue #42 the ledger must inherit the loop's terminal verdict
        rather than inferring success from non-empty text. Only
        ``completed`` maps to ``SUCCEEDED``. ``incomplete`` (max steps,
        partial tool failures, etc.) maps to ``FAILED`` with an explicit
        reason so the canonical ledger never claims success for a run that
        the loop did not actually complete. An empty/unknown terminal state
        is treated as ``unverified`` and likewise never reported as success.
        """
        summary = result.final_response.strip()
        terminal = (result.terminal_state or "").strip()
        if terminal == "completed":
            if not summary:
                return (
                    AgentRunStatus.FAILED,
                    "",
                    "Subagent completed without a final response.",
                )
            return AgentRunStatus.SUCCEEDED, summary, ""
        if terminal == "cancelled":
            return (
                AgentRunStatus.CANCELLED,
                summary,
                result.error or "Subagent run was cancelled.",
            )
        if terminal == "incomplete":
            reason = result.terminal_reason or "incomplete"
            return (
                AgentRunStatus.FAILED,
                summary,
                f"Subagent did not complete ({reason}).",
            )
        if terminal == "failed":
            return (
                AgentRunStatus.FAILED,
                summary,
                f"Subagent run failed: {result.error or result.terminal_reason or 'unknown'}",
            )
        # Unverified: the executor returned without a trusted terminal state
        # (e.g. legacy executor, unexpected early return). Fail-closed.
        return (
            AgentRunStatus.FAILED,
            summary,
            "Subagent finished with unverified terminal state.",
        )

    @staticmethod
    def _apply_terminal(
        run: AgentRun,
        *,
        status: AgentRunStatus,
        summary: str,
        error: str,
        terminal_state: str,
        terminal_reason: str,
    ) -> None:
        run.status = status
        if summary:
            run.summary = summary
        if error:
            run.error = error
        run.ended_at = utc_now()

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
        meta: dict[str, Any] = {
            "requester_session_id": payload.requester_session_id,
            "parent_run_id": run.parent_run_id or "",
            "task_id": run.task_id or "",
            "run_kind": run.kind.value,
        }
        if payload.provider_id:
            meta["provider_id"] = payload.provider_id
        if payload.model:
            meta["model"] = payload.model
        if payload.reasoning_effort:
            existing = await self._memory.get_session_meta(run.session_id)
            runtime = runtime_meta_from_session_meta(existing)
            meta[RUNTIME_META_KEY] = merge_runtime_meta(
                runtime,
                {"reasoning": {"effort": payload.reasoning_effort}},
            )
        await self._memory.update_session_meta(run.session_id, meta)
        await self._seed_child_context(run, payload, spec)

    async def _seed_child_context(
        self,
        run: AgentRun,
        payload: AgentRunPayload,
        spec: SubagentSpec,
    ) -> None:
        if self._memory is None or spec.context_mode == "isolated":
            return
        records = await self._memory.get_recent(payload.requester_session_id, limit=20)
        records = [
            record
            for record in records
            if record.turn.role in {"user", "assistant"} and record.turn.content.strip()
        ]
        if not records:
            return

        if spec.context_mode == "fork":
            for record in records:
                metadata = dict(record.turn.metadata or {})
                metadata["forked_from_session"] = payload.requester_session_id
                metadata["forked_turn_id"] = record.turn_id
                await self._memory.append_turn(
                    run.session_id,
                    ConversationTurn(
                        role=record.turn.role,
                        content=record.turn.content,
                        source=f"subagent_fork:{record.turn.source}",
                        metadata=metadata,
                    ),
                )
            return

        if spec.context_mode == "summary" and not spec.handoff_summary:
            excerpt = self._format_parent_context_excerpt(records)
            if excerpt:
                await self._memory.append_turn(
                    run.session_id,
                    ConversationTurn(
                        role="user",
                        content=(
                            "Parent context excerpt for the delegated task:\n" + excerpt
                        ),
                        source="subagent_context_summary",
                        metadata={"source_session_id": payload.requester_session_id},
                    ),
                )

    @staticmethod
    def _format_parent_context_excerpt(
        records: list[Any], *, max_chars: int = 6000
    ) -> str:
        lines: list[str] = []
        remaining = max_chars
        for record in records:
            label = "User" if record.turn.role == "user" else "Assistant"
            text = " ".join(record.turn.content.split())
            if not text:
                continue
            line = f"{label}: {text}"
            if len(line) > remaining:
                line = line[: max(0, remaining - 3)].rstrip() + "..."
            lines.append(line)
            remaining -= len(line) + 1
            if remaining <= 0:
                break
        return "\n".join(lines)

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
        if run.requester_session_id and self._memory is not None:
            content = (
                f"Subagent task {run.task_id} completed with status {status.value}."
            )
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

        # Issue #41: channel delivery so the user actually sees the result.
        # ``notify_policy=silent`` keeps completion queryable via agent_list /
        # the API but sends nothing to the channel. ``done_only`` (default)
        # dispatches a concise notification through the completion deliverer
        # when a stable target was captured at spawn. A database claim is
        # acquired before the external side effect so concurrent callbacks do
        # not both send. Failed attempts release their exact claim; abandoned
        # claims become reclaimable after the configured lease timeout.
        if self._deliverer is None or run.task_id is None:
            return
        task = await self._task_store.get(run.task_id)
        if task is None or task.delivered_at is not None:
            return
        if not task.delivery_target:
            return
        claimed_at = utc_now()
        stale_before = claimed_at - timedelta(
            seconds=max(1, self._config.delivery_claim_ttl_seconds)
        )
        try:
            claimed = await self._task_store.claim_delivery(
                run.task_id,
                claimed_at=claimed_at,
                stale_before=stale_before,
            )
        except Exception:
            logger.exception("subagent.delivery_claim_failed", task_id=run.task_id)
            return
        if not claimed:
            return

        try:
            delivered = await self._deliverer.deliver(
                task=task,
                status=status,
                summary=summary,
                error=error,
            )
        except Exception:
            logger.exception("subagent.delivery_failed", task_id=run.task_id)
            await self._release_delivery_claim(run.task_id, claimed_at)
            return
        if not delivered:
            await self._release_delivery_claim(run.task_id, claimed_at)
            return

        delivered_at = utc_now()
        try:
            marked = await self._task_store.mark_delivered(
                run.task_id,
                claimed_at=claimed_at,
                delivered_at=delivered_at,
            )
        except Exception:
            logger.exception("subagent.delivery_mark_failed", task_id=run.task_id)
            return
        if marked:
            logger.info(
                "subagent.delivered",
                task_id=run.task_id,
                status=status.value,
                target=task.delivery_target,
            )
        else:
            logger.warning(
                "subagent.delivery_claim_lost",
                task_id=run.task_id,
                target=task.delivery_target,
            )

    async def _release_delivery_claim(self, task_id: str, claimed_at: datetime) -> None:
        try:
            await self._task_store.release_delivery_claim(
                task_id, claimed_at=claimed_at
            )
        except Exception:
            logger.exception("subagent.delivery_release_failed", task_id=task_id)
