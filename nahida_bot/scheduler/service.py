"""Pure-asyncio cron scheduler with SQLite persistence."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

import structlog

from nahida_bot.agent.context import ContextMessage
from nahida_bot.agent.memory.models import ConversationTurn
from nahida_bot.agent.providers import ToolDefinition
from nahida_bot.core.context import SessionContext, current_session
from nahida_bot.plugins.base import OutboundMessage
from nahida_bot.scheduler.models import CronJob, SchedulerConfig
from nahida_bot.scheduler.repository import CronRepository

if TYPE_CHECKING:
    from nahida_bot.agent.loop import AgentLoop
    from nahida_bot.agent.memory.store import MemoryStore
    from nahida_bot.agent.providers.manager import ProviderManager
    from nahida_bot.core.channel_registry import ChannelRegistry
    from nahida_bot.plugins.registry import ToolRegistry
    from nahida_bot.workspace.manager import WorkspaceManager

    from nahida_bot.core.router import MessageRouter

logger = structlog.get_logger(__name__)

_CRON_TOOL_NAMES = frozenset(
    {"cron_create", "cron_update", "cron_list", "cron_cancel", "cron_delete"}
)


class SchedulerService:
    """In-process cron scheduler backed by SQLite.

    Uses a poll loop to check for due jobs, then fires them via
    the AgentLoop and sends responses through the originating channel.
    """

    def __init__(
        self,
        repo: CronRepository,
        *,
        agent_loop: AgentLoop | None = None,
        memory_store: MemoryStore | None = None,
        channel_registry: ChannelRegistry | None = None,
        provider_manager: ProviderManager | None = None,
        workspace_manager: WorkspaceManager | None = None,
        tool_registry: ToolRegistry | None = None,
        message_router: MessageRouter | None = None,
        system_prompt: str = "You are a helpful assistant.",
        config: SchedulerConfig | None = None,
    ) -> None:
        self._repo = repo
        self._agent = agent_loop
        self._memory = memory_store
        self._channels = channel_registry
        self._providers = provider_manager
        self._workspace = workspace_manager
        self._tools = tool_registry
        self._router = message_router
        self._system_prompt = system_prompt
        self._config = config or SchedulerConfig()

        self._poll_task: asyncio.Task[None] | None = None
        self._active_tasks: set[asyncio.Task[None]] = set()
        self._running = False

    def wire_runtime(
        self,
        *,
        message_router: MessageRouter | None = None,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        """Wire late-bound runtime dependencies (called after plugins load)."""
        if message_router is not None:
            self._router = message_router
        if tool_registry is not None:
            self._tools = tool_registry

    # ── Lifecycle ─────────────────────────────────────────

    async def start(self) -> None:
        """Start the scheduler poll loop."""
        if self._running:
            return

        # Recover persisted active jobs
        active = await self._repo.get_all_active_jobs()
        if active:
            logger.info(
                "scheduler.recovered_jobs",
                count=len(active),
                jobs=[j.job_id for j in active],
            )

        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("scheduler.started")

    async def stop(self) -> None:
        """Stop the scheduler and wait for in-flight tasks."""
        if not self._running:
            return
        self._running = False

        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

        # Wait for in-flight fire tasks
        if self._active_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._active_tasks, return_exceptions=True),
                    timeout=5.0,
                )
            except TimeoutError:
                logger.warning(
                    "scheduler.stop_timeout", pending=len(self._active_tasks)
                )
            self._active_tasks.clear()

        logger.info("scheduler.stopped")

    # ── Public API ────────────────────────────────────────

    async def create_job(
        self,
        *,
        platform: str,
        chat_id: str,
        prompt: str,
        mode: Literal["once", "interval"],
        fire_at: str | None = None,
        interval_seconds: int | None = None,
        max_runs: int | None = None,
        workspace_id: str | None = None,
    ) -> CronJob:
        """Create and persist a new scheduled job."""
        now = datetime.now(UTC)
        job_id = uuid4().hex[:16]
        session_key = f"{platform}:{chat_id}"
        self._validate_prompt(prompt)
        self._validate_max_runs(max_runs)
        await self._check_chat_quota(session_key)

        # Compute next_fire_at
        if mode == "once":
            if fire_at is None:
                raise ValueError("fire_at is required for mode='once'")
            next_fire_at = self._normalize_fire_at(fire_at, now=now)
        elif mode == "interval":
            self._validate_interval(interval_seconds)
            assert interval_seconds is not None
            next_fire_at = (now + timedelta(seconds=interval_seconds)).isoformat()
        else:
            raise ValueError(f"Invalid mode: {mode}")

        job = CronJob(
            job_id=job_id,
            platform=platform,
            chat_id=chat_id,
            session_key=session_key,
            prompt=prompt,
            mode=mode,
            fire_at=next_fire_at if mode == "once" else None,
            interval_seconds=interval_seconds if mode == "interval" else None,
            max_runs=max_runs,
            run_count=0,
            is_active=True,
            created_at=now.isoformat(),
            next_fire_at=next_fire_at,
            last_fired_at=None,
            workspace_id=workspace_id,
        )

        await self._repo.insert_job(job)
        logger.info(
            "scheduler.job_created",
            job_id=job_id,
            mode=mode,
            next_fire_at=next_fire_at,
        )
        return job

    async def update_job(
        self,
        job_id: str,
        *,
        prompt: str | None = None,
        mode: Literal["once", "interval"] | None = None,
        fire_at: str | None = None,
        interval_seconds: int | None = None,
        max_runs: int | None = None,
    ) -> CronJob:
        """Update an active scheduled job.

        Returns the updated job. Raises ValueError for invalid input and
        RuntimeError if the job is inactive or currently running.
        """
        existing = await self._repo.get_job(job_id)
        if existing is None:
            raise ValueError(f"Job '{job_id}' not found")
        if not existing.is_active:
            raise RuntimeError(f"Job '{job_id}' is inactive or completed")
        if existing.claimed_at is not None:
            raise RuntimeError(f"Job '{job_id}' is currently running")

        now = datetime.now(UTC)
        new_prompt = prompt if prompt is not None else existing.prompt
        self._validate_prompt(new_prompt)

        new_mode = mode or existing.mode
        self._validate_max_runs(max_runs)
        new_max_runs = max_runs if max_runs is not None else existing.max_runs

        if new_mode == "once":
            new_fire_at = fire_at if fire_at is not None else existing.fire_at
            if new_fire_at is None:
                raise ValueError("fire_at is required for mode='once'")
            next_fire_at = self._normalize_fire_at(new_fire_at, now=now)
            new_interval_seconds = None
            stored_fire_at = next_fire_at
            new_max_runs = None
        elif new_mode == "interval":
            new_interval_seconds = (
                interval_seconds
                if interval_seconds is not None
                else existing.interval_seconds
            )
            self._validate_interval(new_interval_seconds)
            assert new_interval_seconds is not None
            stored_fire_at = None
            if interval_seconds is not None or mode == "interval":
                next_fire_at = (
                    now + timedelta(seconds=new_interval_seconds)
                ).isoformat()
            else:
                next_fire_at = existing.next_fire_at
        else:
            raise ValueError(f"Invalid mode: {new_mode}")

        updated = await self._repo.update_job(
            job_id,
            prompt=new_prompt,
            mode=new_mode,
            fire_at=stored_fire_at,
            interval_seconds=new_interval_seconds,
            max_runs=new_max_runs,
            next_fire_at=next_fire_at,
        )
        if not updated:
            raise RuntimeError(f"Job '{job_id}' is inactive or currently running")

        job = await self._repo.get_job(job_id)
        if job is None:
            raise RuntimeError(f"Job '{job_id}' disappeared during update")
        logger.info("scheduler.job_updated", job_id=job_id, mode=new_mode)
        return job

    async def list_jobs(self, platform: str, chat_id: str) -> list[CronJob]:
        """List active jobs for a specific chat."""
        session_key = f"{platform}:{chat_id}"
        return await self._repo.get_jobs_by_chat(session_key, active_only=True)

    async def get_job(self, job_id: str) -> CronJob | None:
        """Look up a single job by ID."""
        return await self._repo.get_job(job_id)

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a job. Returns True if it was active and cancelled."""
        cancelled = await self._repo.cancel_job(job_id)
        if cancelled:
            logger.info("scheduler.job_cancelled", job_id=job_id)
        return cancelled

    async def delete_job(self, job_id: str) -> bool:
        """Permanently delete a job from persistence."""
        deleted = await self._repo.delete_job(job_id)
        if deleted:
            logger.info("scheduler.job_deleted", job_id=job_id)
        return deleted

    # ── Internal ──────────────────────────────────────────

    async def _poll_loop(self) -> None:
        """Background poll loop: check for due jobs and fire them."""
        try:
            while self._running:
                try:
                    available = self._config.max_concurrent_fires - len(
                        self._active_tasks
                    )
                    if available <= 0:
                        await asyncio.sleep(self._config.poll_interval_seconds)
                        continue

                    now_iso = datetime.now(UTC).isoformat()
                    due_jobs = await self._repo.claim_due_jobs(now_iso, limit=available)

                    for job in due_jobs:
                        self._dispatch_fire(job)

                except Exception:
                    logger.exception("scheduler.poll_error")

                await asyncio.sleep(self._config.poll_interval_seconds)
        except asyncio.CancelledError:
            return

    def _dispatch_fire(self, job: CronJob) -> None:
        """Dispatch a fire task (non-blocking)."""
        task = asyncio.create_task(self._fire_job(job))
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)

    async def _fire_job(self, job: CronJob) -> None:
        """Execute a scheduled job: run agent and send response."""
        try:
            await asyncio.wait_for(
                self._execute_fire(job),
                timeout=self._config.job_timeout_seconds,
            )
        except TimeoutError:
            logger.warning(
                "scheduler.fire_timeout",
                job_id=job.job_id,
                timeout=self._config.job_timeout_seconds,
            )
            await self._mark_failed(job, "timeout")
            await self._send_error(job, "Scheduled task timed out.")
        except Exception as exc:
            logger.exception("scheduler.fire_error", job_id=job.job_id)
            await self._mark_failed(job, type(exc).__name__)
            await self._send_error(job, "Scheduled task failed.")
        else:
            fired_at = datetime.now(UTC).isoformat()
            next_fire = self._compute_next_fire(job, fired_at)
            await self._repo.complete_fire(
                job.job_id, next_fire_at=next_fire, fired_at=fired_at
            )

    def _compute_next_fire(self, job: CronJob, now_iso: str) -> str | None:
        """Compute the next fire time after marking fired. None = done."""
        if job.mode == "once":
            return None  # One-shot: done after first fire

        # Interval mode
        if job.interval_seconds is None:
            return None

        new_run_count = job.run_count + 1
        if job.max_runs is not None and new_run_count >= job.max_runs:
            return None  # Reached max runs

        now = datetime.fromisoformat(now_iso)
        return (now + timedelta(seconds=job.interval_seconds)).isoformat()

    async def _mark_failed(self, job: CronJob, error: str) -> None:
        next_failure_count = job.failure_count + 1
        deactivate = next_failure_count >= self._config.max_consecutive_failures
        retry_at = (
            datetime.now(UTC) + timedelta(seconds=self._config.failure_retry_seconds)
        ).isoformat()
        await self._repo.mark_failed(
            job.job_id,
            retry_at=retry_at,
            error=error,
            deactivate=deactivate,
        )

    async def _execute_fire(self, job: CronJob) -> None:
        """Run the agent with the job's prompt and send the response."""
        if self._agent is None:
            logger.warning("scheduler.no_agent", job_id=job.job_id)
            return

        # Resolve current active session for the chat
        session_id = job.session_key
        if self._router is not None:
            session_id = self._router.get_active_session_id(job.platform, job.chat_id)

        # Set session context for tool handlers
        ctx_token = current_session.set(
            SessionContext(
                platform=job.platform,
                chat_id=job.chat_id,
                session_id=session_id,
                workspace_id=job.workspace_id,
            )
        )
        try:
            await self._do_fire(job, session_id)
        finally:
            current_session.reset(ctx_token)

    async def _do_fire(self, job: CronJob, session_id: str) -> None:
        """The actual agent execution + response delivery."""
        # Resolve provider
        provider_slot = await self._resolve_provider(session_id)

        # Load history
        history = await self._load_history(session_id)

        # Resolve workspace
        workspace_root = self._resolve_workspace_root(job.workspace_id)

        # Build tools
        tools = self._registered_tools()

        # Run agent
        run_kwargs: dict[str, Any] = {
            "user_message": job.prompt,
            "system_prompt": self._system_prompt,
            "history_messages": history,
        }
        if workspace_root is not None:
            run_kwargs["workspace_root"] = workspace_root
        if tools:
            run_kwargs["tools"] = tools
        if provider_slot is not None:
            run_kwargs["provider"] = provider_slot.provider
            run_kwargs["context_builder"] = provider_slot.context_builder

        assert self._agent is not None  # guarded by _execute_fire
        result = await self._agent.run(**run_kwargs)

        # Persist turns
        if self._memory is not None:
            user_turn = ConversationTurn(
                role="user", content=job.prompt, source="cron_trigger"
            )
            await self._memory.append_turn(session_id, user_turn)

            if result.final_response:
                assistant_turn = ConversationTurn(
                    role="assistant",
                    content=result.final_response,
                    source="agent_response",
                )
                await self._memory.append_turn(session_id, assistant_turn)

        # Send response via channel
        if result.final_response and self._channels is not None:
            channel = self._channels.get(job.platform)
            if channel is not None:
                await channel.send_message(
                    job.chat_id,
                    OutboundMessage(text=result.final_response),
                )
            else:
                logger.warning(
                    "scheduler.no_channel",
                    job_id=job.job_id,
                    platform=job.platform,
                )

        logger.info(
            "scheduler.fired",
            job_id=job.job_id,
            session_id=session_id,
            response_len=len(result.final_response),
        )

    async def _send_error(self, job: CronJob, message: str) -> None:
        """Send a brief error message to the originating chat."""
        if self._channels is None:
            return
        channel = self._channels.get(job.platform)
        if channel is not None:
            try:
                await channel.send_message(
                    job.chat_id,
                    OutboundMessage(text=f"[Scheduler] {message}"),
                )
            except Exception:
                logger.exception("scheduler.send_error_failed", job_id=job.job_id)

    # ── Helpers (mirror MessageRouter logic) ──────────────

    async def _resolve_provider(self, session_id: str) -> Any:
        """Resolve provider slot, same logic as MessageRouter._resolve_provider."""
        if self._providers is None:
            return None
        if self._memory is not None:
            meta = await self._memory.get_session_meta(session_id)
            if meta:
                model = meta.get("model")
                if model:
                    slot = self._providers.resolve_model(model)
                    if slot is not None:
                        return slot
                provider_id = meta.get("provider_id")
                if provider_id:
                    slot = self._providers.get(provider_id)
                    if slot is not None:
                        return slot
        return self._providers.default

    async def _load_history(self, session_id: str) -> list[ContextMessage]:
        """Load conversation history for a session."""
        if self._memory is None:
            return []
        await self._memory.ensure_session(session_id)
        records = await self._memory.get_recent(session_id, limit=50)
        return [
            ContextMessage(
                role=r.turn.role,  # type: ignore[arg-type]
                content=r.turn.content,
                source=r.turn.source,
            )
            for r in records
        ]

    def _resolve_workspace_root(self, workspace_id: str | None) -> Any:
        """Resolve workspace root path."""
        if self._workspace is None or workspace_id is None:
            return None
        return self._workspace.workspace_path(workspace_id)

    def _registered_tools(self) -> list[ToolDefinition]:
        """Collect all registered plugin tools as provider definitions."""
        if self._tools is None:
            return []
        return [
            ToolDefinition(
                name=entry.name,
                description=entry.description,
                parameters=entry.parameters,
            )
            for entry in self._tools.all()
            if entry.name not in _CRON_TOOL_NAMES
        ]

    def _validate_prompt(self, prompt: str) -> None:
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        if len(prompt) > self._config.max_prompt_chars:
            raise ValueError(
                f"prompt must be <= {self._config.max_prompt_chars} characters"
            )

    def _validate_interval(self, interval_seconds: int | None) -> None:
        if (
            interval_seconds is None
            or interval_seconds < self._config.min_interval_seconds
        ):
            raise ValueError(
                "interval_seconds must be >= "
                f"{self._config.min_interval_seconds} for mode='interval'"
            )

    @staticmethod
    def _validate_max_runs(max_runs: int | None) -> None:
        if max_runs is not None and max_runs <= 0:
            raise ValueError("max_runs must be > 0")

    @staticmethod
    def _normalize_fire_at(fire_at: str, *, now: datetime) -> str:
        dt = datetime.fromisoformat(fire_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        dt = dt.astimezone(UTC)
        if dt <= now:
            raise ValueError("fire_at must be in the future")
        return dt.isoformat()

    async def _check_chat_quota(self, session_key: str) -> None:
        active_count = await self._repo.count_active_jobs_by_chat(session_key)
        if active_count >= self._config.max_jobs_per_chat:
            raise ValueError(
                "active scheduled task limit reached for this chat "
                f"({self._config.max_jobs_per_chat})"
            )
