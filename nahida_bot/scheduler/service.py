"""Pure-asyncio cron scheduler with SQLite persistence."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, cast

from croniter import croniter
from uuid import uuid4

import structlog

from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.context import SessionContext, current_session
from nahida_bot.agent.memory.consolidation import MemoryConsolidator
from nahida_bot.core.sentinel import detect_sentinel
from nahida_bot.plugins.base import MessageContext, OutboundMessage
from nahida_bot.scheduler.models import CronJob, SchedulerConfig
from nahida_bot.scheduler.repository import CronRepository

if TYPE_CHECKING:
    from nahida_bot.core.channel_registry import ChannelRegistry
    from nahida_bot.core.router import MessageRouter
    from nahida_bot.core.session_runner import SessionRunner
    from nahida_bot.db.repositories.sqlite_message_delivery_repo import (
        SQLiteMessageDeliveryStore,
    )

logger = structlog.get_logger(__name__)

_CRON_TOOL_NAMES = frozenset(
    {"cron_create", "cron_update", "cron_list", "cron_cancel", "cron_delete"}
)


class SchedulerService:
    """In-process cron scheduler backed by SQLite.

    Uses a poll loop to check for due jobs, then fires them via
    the SessionRunner and sends responses through the originating channel.
    """

    def __init__(
        self,
        repo: CronRepository,
        *,
        runner: SessionRunner | None = None,
        channel_registry: ChannelRegistry | None = None,
        message_delivery_store: SQLiteMessageDeliveryStore | None = None,
        message_router: MessageRouter | None = None,
        system_prompt: str = "You are a helpful assistant.",
        app_name: str = "the assistant",
        config: SchedulerConfig | None = None,
        enable_silent_reply: bool = True,
    ) -> None:
        self._repo = repo
        self._runner = runner
        self._channels = channel_registry
        self._message_delivery_store = message_delivery_store
        self._router = message_router
        self._system_prompt = system_prompt
        self._app_name = app_name
        self._config = config or SchedulerConfig()
        self._enable_silent_reply = enable_silent_reply
        # Optional callback: on_job_event("fired" | "failed", job_id, **kwargs)
        # Set by EventBroadcaster to push SSE events when jobs fire/fail.
        self.on_job_event: Any = None

        self._poll_task: asyncio.Task[None] | None = None
        self._memory_dream_task: asyncio.Task[None] | None = None
        self._memory_dream_next_at: datetime | None = None
        self._active_tasks: set[asyncio.Task[None]] = set()
        self._running = False

    def wire_runtime(
        self,
        *,
        message_router: MessageRouter | None = None,
        message_delivery_store: SQLiteMessageDeliveryStore | None = None,
    ) -> None:
        """Wire late-bound runtime dependencies (called after plugins load)."""
        if message_router is not None:
            self._router = message_router
        if message_delivery_store is not None:
            self._message_delivery_store = message_delivery_store

    # ── Lifecycle ─────────────────────────────────────────

    async def start(self) -> None:
        """Start the scheduler poll loop."""
        if self._running:
            return

        # Release orphaned claims from previous crash
        released = await self._repo.release_stale_claims()
        if released:
            logger.info("scheduler.released_stale_claims", count=released)

        # Recover persisted active jobs
        active = await self._repo.get_all_active_jobs()
        if active:
            logger.info(
                "scheduler.recovered_jobs",
                count=len(active),
                jobs=[j.job_id for j in active],
            )

        self._running = True
        if self._config.memory_dreaming_enabled:
            self._memory_dream_next_at = datetime.now(UTC) + timedelta(
                seconds=self._config.memory_dreaming_initial_delay_seconds
            )
            logger.info(
                "scheduler.memory_dreaming_scheduled",
                next_at=self._memory_dream_next_at.isoformat(),
                interval_seconds=self._config.memory_dreaming_interval_seconds,
                provider_id=self._config.memory_dreaming_provider_id,
                model=self._config.memory_dreaming_model,
            )
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
            graceful_timeout = min(self._config.job_timeout_seconds, 30.0)
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._active_tasks, return_exceptions=True),
                    timeout=graceful_timeout,
                )
            except TimeoutError:
                logger.warning(
                    "scheduler.stop_timeout",
                    pending=len(self._active_tasks),
                    timeout=graceful_timeout,
                )
                for task in self._active_tasks:
                    task.cancel()
            self._active_tasks.clear()

        logger.info("scheduler.stopped")

    # ── Public API ────────────────────────────────────────

    async def create_job(
        self,
        *,
        address: ChatAddress,
        prompt: str,
        mode: Literal["once", "interval", "cron"],
        fire_at: str | None = None,
        interval_seconds: int | None = None,
        cron_expression: str | None = None,
        max_runs: int | None = None,
        workspace_id: str | None = None,
        session_mode: Literal["main", "isolated", "fresh", "named"] = "main",
        session_name: str | None = None,
        created_by_user_id: str = "",
        created_from_session_id: str = "",
        created_from_chat_address: str = "",
    ) -> CronJob:
        """Create and persist a new scheduled job at a typed address."""
        if not address.is_typed:
            raise ValueError("Cron jobs require a typed chat target")
        if session_mode not in {"main", "isolated", "fresh", "named"}:
            raise ValueError(
                "session_mode must be one of: main, isolated, fresh, named"
            )
        if session_mode == "named":
            if not session_name or not re.match(r"^[a-zA-Z0-9_-]+$", session_name):
                raise ValueError(
                    "session_name is required for session_mode='named' "
                    "and must contain only letters, digits, hyphens, and underscores"
                )
        now = datetime.now(UTC)
        job_id = uuid4().hex[:16]
        self._validate_prompt(prompt)
        self._validate_max_runs(max_runs)

        # Compute next_fire_at
        if mode == "once":
            if fire_at is None:
                raise ValueError("fire_at is required for mode='once'")
            next_fire_at = self._normalize_fire_at(fire_at, now=now)
            stored_cron = None
        elif mode == "interval":
            self._validate_interval(interval_seconds)
            assert interval_seconds is not None
            next_fire_at = (now + timedelta(seconds=interval_seconds)).isoformat()
            stored_cron = None
        elif mode == "cron":
            next_fire_at = self._validate_and_get_next_cron(cron_expression, now=now)
            stored_cron = cron_expression
        else:
            raise ValueError(f"Invalid mode: {mode}")

        job = CronJob(
            job_id=job_id,
            platform=address.channel,
            chat_id=address.target_id,
            session_key=address.chat_key,
            prompt=prompt,
            mode=mode,
            fire_at=next_fire_at if mode == "once" else None,
            interval_seconds=interval_seconds if mode == "interval" else None,
            cron_expression=stored_cron,
            max_runs=max_runs,
            run_count=0,
            is_active=True,
            created_at=now.isoformat(),
            next_fire_at=next_fire_at,
            last_fired_at=None,
            workspace_id=workspace_id,
            session_mode=session_mode,
            session_name=session_name if session_mode == "named" else None,
            chat_type=address.target_type,
            created_by_user_id=created_by_user_id,
            created_from_session_id=created_from_session_id,
            created_from_chat_address=created_from_chat_address,
        )

        await self._repo.insert_job_with_quota(
            job, max_per_chat=self._config.max_jobs_per_chat
        )
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
        mode: Literal["once", "interval", "cron"] | None = None,
        fire_at: str | None = None,
        interval_seconds: int | None = None,
        cron_expression: str | None = None,
        max_runs: int | None = None,
        session_mode: Literal["main", "isolated", "fresh", "named"] | None = None,
        session_name: str | None = None,
    ) -> CronJob:
        """Update a scheduled job that is not currently running.

        Returns the updated job. Raises ValueError for invalid input and
        RuntimeError if the job is currently running.
        """
        existing = await self._repo.get_job(job_id)
        if existing is None:
            raise ValueError(f"Job '{job_id}' not found")
        if existing.claimed_at is not None:
            raise RuntimeError(f"Job '{job_id}' is currently running")

        now = datetime.now(UTC)
        new_prompt = prompt if prompt is not None else existing.prompt
        self._validate_prompt(new_prompt)
        new_session_mode, new_session_name = self._resolve_session_mode_update(
            existing,
            session_mode=session_mode,
            session_name=session_name,
        )

        new_mode = mode or existing.mode
        mode_changed = mode is not None and mode != existing.mode
        self._validate_max_runs(max_runs)
        new_max_runs = max_runs if max_runs is not None else existing.max_runs

        if new_mode == "once":
            new_fire_at = fire_at if fire_at is not None else existing.fire_at
            if new_fire_at is None:
                raise ValueError("fire_at is required for mode='once'")
            new_interval_seconds = None
            new_cron_expression = None
            new_max_runs = None
            fire_at_changed = fire_at is not None and fire_at != existing.fire_at
            if existing.is_active or mode_changed or fire_at_changed:
                next_fire_at = self._normalize_fire_at(new_fire_at, now=now)
                stored_fire_at = next_fire_at
            else:
                next_fire_at = existing.next_fire_at
                stored_fire_at = existing.fire_at
        elif new_mode == "interval":
            new_interval_seconds = (
                interval_seconds
                if interval_seconds is not None
                else existing.interval_seconds
            )
            self._validate_interval(new_interval_seconds)
            assert new_interval_seconds is not None
            stored_fire_at = None
            new_cron_expression = None
            if existing.is_active and (interval_seconds is not None or mode_changed):
                next_fire_at = (
                    now + timedelta(seconds=new_interval_seconds)
                ).isoformat()
            elif not existing.is_active and (
                interval_seconds is not None or mode_changed
            ):
                next_fire_at = (
                    now + timedelta(seconds=new_interval_seconds)
                ).isoformat()
            else:
                next_fire_at = existing.next_fire_at
        elif new_mode == "cron":
            new_cron_expression = (
                cron_expression
                if cron_expression is not None
                else existing.cron_expression
            )
            stored_fire_at = None
            new_interval_seconds = None
            if existing.is_active or mode_changed or cron_expression is not None:
                next_fire_at = self._validate_and_get_next_cron(
                    new_cron_expression, now=now
                )
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
            cron_expression=new_cron_expression,
            max_runs=new_max_runs,
            next_fire_at=next_fire_at,
            session_mode=new_session_mode,
            session_name=new_session_name,
        )
        if not updated:
            raise RuntimeError(f"Job '{job_id}' is currently running")

        job = await self._repo.get_job(job_id)
        if job is None:
            raise RuntimeError(f"Job '{job_id}' disappeared during update")
        logger.info("scheduler.job_updated", job_id=job_id, mode=new_mode)
        return job

    async def list_jobs(self, address: ChatAddress) -> list[CronJob]:
        """List active jobs for a specific chat.

        Returns typed jobs for the chat plus legacy jobs created before typed
        chat keys existed.
        """
        jobs: list[CronJob] = []
        seen: set[str] = set()

        if address.is_typed:
            for job in await self._repo.get_jobs_by_chat(
                address.chat_key, active_only=True
            ):
                jobs.append(job)
                seen.add(job.job_id)

        # Fallback to legacy key
        for job in await self._repo.get_jobs_by_chat(
            address.legacy_key, active_only=True
        ):
            if job.job_id not in seen:
                jobs.append(job)
        return jobs

    async def get_job(self, job_id: str) -> CronJob | None:
        """Look up a single job by ID."""
        return await self._repo.get_job(job_id)

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a job. Returns True if it was active and cancelled."""
        cancelled = await self._repo.cancel_job(job_id)
        if cancelled:
            logger.info("scheduler.job_cancelled", job_id=job_id)
        return cancelled

    async def activate_job(self, job_id: str) -> CronJob:
        """Reactivate an inactive job using the next valid fire time."""
        existing = await self._repo.get_job(job_id)
        if existing is None:
            raise ValueError(f"Job '{job_id}' not found")
        if existing.is_active:
            raise RuntimeError(f"Job '{job_id}' is already active")
        if existing.claimed_at is not None:
            raise RuntimeError(f"Job '{job_id}' is currently running")

        next_fire_at = self._compute_activation_next_fire(
            existing,
            now=datetime.now(UTC),
        )
        activated = await self._repo.activate_job(
            job_id,
            next_fire_at=next_fire_at,
        )
        if not activated:
            raise RuntimeError(f"Job '{job_id}' is already active or currently running")

        job = await self._repo.get_job(job_id)
        if job is None:
            raise RuntimeError(f"Job '{job_id}' disappeared during activation")
        logger.info("scheduler.job_activated", job_id=job_id, mode=job.mode)
        return job

    async def delete_job(self, job_id: str) -> bool:
        """Permanently delete a job from persistence."""
        deleted = await self._repo.delete_job(job_id)
        if deleted:
            logger.info("scheduler.job_deleted", job_id=job_id)
        return deleted

    async def list_all_jobs(
        self,
        *,
        active: str = "all",
        limit: int = 100,
    ) -> list[CronJob]:
        """List all jobs across all chats.

        Unlike list_jobs(address), this returns every job in the system,
        useful for the admin WebUI.

        Args:
            active: "true" = active only, "false" = inactive only,
                    "all" = everything.
            limit: Maximum number of jobs to return.
        """
        return await self._repo.list_all_jobs(active=active, limit=limit)

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

                    self._dispatch_memory_dreaming_if_due()

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

    def _dispatch_memory_dreaming_if_due(self) -> None:
        """Dispatch the internal memory dreaming job when its interval elapses."""
        if not self._config.memory_dreaming_enabled:
            return
        if self._memory_dream_task is not None and not self._memory_dream_task.done():
            return
        now = datetime.now(UTC)
        if self._memory_dream_next_at is None:
            self._memory_dream_next_at = now + timedelta(
                seconds=self._config.memory_dreaming_interval_seconds
            )
            return
        if now < self._memory_dream_next_at:
            return
        self._memory_dream_next_at = now + timedelta(
            seconds=self._config.memory_dreaming_interval_seconds
        )
        logger.debug(
            "scheduler.memory_dreaming_dispatch",
            next_at=self._memory_dream_next_at.isoformat(),
        )
        task = asyncio.create_task(self._run_memory_dreaming_safe())
        self._memory_dream_task = task
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)

    async def _run_memory_dreaming_safe(self) -> None:
        try:
            await self._run_memory_dreaming_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("scheduler.memory_dreaming_failed")

    async def _run_memory_dreaming_once(self) -> int:
        """Run one internal memory dreaming pass across recently active sessions."""
        if self._runner is None:
            return 0
        memory = self._runner.memory
        if memory is None:
            return 0
        provider_manager = self._runner.provider_manager
        if provider_manager is None:
            return 0

        sessions = await memory.list_sessions(
            limit=self._config.memory_dreaming_session_limit
        )
        logger.debug(
            "scheduler.memory_dreaming_run_start",
            session_count=len(sessions),
            session_limit=self._config.memory_dreaming_session_limit,
        )
        applied_total = 0
        processed_sessions = 0
        for session in sessions:
            try:
                applied = await self._dream_session(
                    session.session_id,
                    workspace_id=session.workspace_id,
                )
                if applied:
                    processed_sessions += 1
                applied_total += applied
            except Exception as exc:
                logger.warning(
                    "scheduler.memory_dreaming_session_failed",
                    session_id=session.session_id,
                    error=str(exc),
                )
        logger.info(
            "scheduler.memory_dreaming_completed",
            applied=applied_total,
            processed_sessions=processed_sessions,
            scanned_sessions=len(sessions),
        )
        return applied_total

    async def _dream_session(
        self, session_id: str, *, workspace_id: str | None = None
    ) -> int:
        assert self._runner is not None
        memory = self._runner.memory
        if memory is None:
            return 0

        meta = await memory.get_session_meta(session_id)
        last_turn_id = _safe_int(meta.get("memory_dream_last_turn_id"), default=0)
        records = await memory.get_recent(
            session_id, limit=self._config.memory_dreaming_recent_turn_limit
        )
        new_records = [record for record in records if record.turn_id > last_turn_id]
        if len(new_records) < 2:
            logger.debug(
                "scheduler.memory_dreaming_session_skipped",
                session_id=session_id,
                reason="not_enough_new_turns",
                new_turns=len(new_records),
                last_turn_id=last_turn_id,
            )
            return 0

        resolved = await self._resolve_memory_dream_provider(session_id)
        if resolved is None:
            logger.debug(
                "scheduler.memory_dreaming_session_skipped",
                session_id=session_id,
                reason="no_dream_provider",
            )
            return 0
        provider_slot, selected_model, provider_reason = resolved

        logger.debug(
            "scheduler.memory_dreaming_session_start",
            session_id=session_id,
            new_turns=len(new_records),
            last_turn_id=last_turn_id,
            provider_id=provider_slot.id,
            model=selected_model or provider_slot.default_model,
            provider_reason=provider_reason,
        )

        workspace_id = workspace_id or str(meta.get("workspace_id") or "") or None
        workspace_root = self._runner.workspace_root_for(workspace_id)
        conversation = "\n".join(
            f"{record.turn.role}: {record.turn.content}"
            for record in new_records
            if record.turn.content.strip()
        )
        if not conversation.strip():
            logger.debug(
                "scheduler.memory_dreaming_session_skipped",
                session_id=session_id,
                reason="empty_conversation",
            )
            return 0

        consolidator = MemoryConsolidator(memory, app_name=self._app_name)
        applied = await consolidator.consolidate_turn(
            session_id=session_id,
            user_message=conversation,
            assistant_message="",
            workspace_id=workspace_id,
            workspace_root=workspace_root,
            dream_provider=provider_slot.provider,
            dream_model=selected_model,
            run_rules=False,
        )
        if applied:
            await self._refresh_memory_embeddings()
        max_turn_id = max(record.turn_id for record in new_records)
        await memory.update_session_meta(
            session_id,
            {
                "memory_dream_last_turn_id": max_turn_id,
                "memory_dream_last_at": datetime.now(UTC).isoformat(),
            },
        )
        logger.debug(
            "scheduler.memory_dreaming_session_done",
            session_id=session_id,
            applied=applied,
            max_turn_id=max_turn_id,
        )
        return applied

    async def _refresh_memory_embeddings(self) -> None:
        """Refresh durable memory embeddings after background dreaming changes."""
        if self._runner is None:
            return
        memory = self._runner.memory
        provider = self._runner.memory_embedding_provider
        if memory is None or provider is None:
            return
        embed_items = getattr(memory, "embed_items", None)
        if not callable(embed_items):
            return
        try:
            count = await cast(Any, embed_items)(
                provider,
                vector_index=self._runner.memory_vector_index,
            )
            logger.debug("scheduler.memory_embeddings_refreshed", count=count)
        except Exception as exc:
            logger.warning("scheduler.memory_embedding_failed", error=str(exc))

    async def _resolve_memory_dream_provider(
        self, session_id: str
    ) -> tuple[Any, str | None, str] | None:
        """Resolve provider/model for memory dreaming via ModelRouter."""
        if self._runner is None:
            return None

        router = self._runner.model_router
        if router is not None:
            # Build explicit override from legacy config fields
            explicit = _legacy_model_spec(
                provider_id=self._config.memory_dreaming_provider_id,
                model=self._config.memory_dreaming_model,
            )
            result = router.resolve_for_task(
                "memory_dreaming",
                explicit=explicit,
                default_spec="memory",
                fallback="session",
            )
            if result is not None:
                return (result.slot, result.model, result.reason)

        # Fallback to session provider
        if self._runner.provider_manager is None:
            return None
        provider_slot, selected_model = await self._runner.resolve_provider_for_session(
            session_id
        )
        if provider_slot is None:
            return None
        return provider_slot, selected_model, "session_provider"

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
            if self.on_job_event is not None:
                await self.on_job_event(
                    "failed", job.job_id, success=False, error="timeout"
                )
        except asyncio.CancelledError:
            logger.info("scheduler.fire_cancelled", job_id=job.job_id)
            await self._mark_failed(job, "cancelled")
            if self.on_job_event is not None:
                await self.on_job_event(
                    "failed", job.job_id, success=False, error="cancelled"
                )
            raise
        except Exception as exc:
            logger.exception("scheduler.fire_error", job_id=job.job_id)
            await self._mark_failed(job, f"{type(exc).__name__}: {exc}")
            await self._send_error(job, "Scheduled task failed.")
            if self.on_job_event is not None:
                await self.on_job_event(
                    "failed", job.job_id, success=False, error=str(exc)
                )
        else:
            fired_at = datetime.now(UTC).isoformat()
            next_fire = self._compute_next_fire(job, fired_at)
            await self._repo.complete_fire(
                job.job_id, next_fire_at=next_fire, fired_at=fired_at
            )
            if self.on_job_event is not None:
                await self.on_job_event("fired", job.job_id, success=True)

    def _compute_next_fire(self, job: CronJob, now_iso: str) -> str | None:
        """Compute the next fire time after marking fired. None = done."""
        if job.mode == "once":
            return None  # One-shot: done after first fire

        new_run_count = job.run_count + 1
        if job.max_runs is not None and new_run_count >= job.max_runs:
            return None  # Reached max runs

        if job.mode == "interval":
            if job.interval_seconds is None:
                return None
            now = datetime.fromisoformat(now_iso)
            return (now + timedelta(seconds=job.interval_seconds)).isoformat()

        if job.mode == "cron":
            if not job.cron_expression:
                return None
            now = datetime.fromisoformat(now_iso)
            cron = croniter(job.cron_expression, now)
            return cron.get_next(datetime).isoformat()

        return None

    def _compute_activation_next_fire(self, job: CronJob, *, now: datetime) -> str:
        if job.max_runs is not None and job.run_count >= job.max_runs:
            raise ValueError(
                "max_runs has already been reached; increase max_runs before activation"
            )

        if job.mode == "once":
            if not job.fire_at:
                raise ValueError("fire_at is required before activation")
            return self._normalize_fire_at(job.fire_at, now=now)

        if job.mode == "interval":
            self._validate_interval(job.interval_seconds)
            assert job.interval_seconds is not None
            return (now + timedelta(seconds=job.interval_seconds)).isoformat()

        if job.mode == "cron":
            return self._validate_and_get_next_cron(job.cron_expression, now=now)

        raise ValueError(f"Invalid mode: {job.mode}")

    @staticmethod
    def _resolve_session_mode_update(
        existing: CronJob,
        *,
        session_mode: Literal["main", "isolated", "fresh", "named"] | None,
        session_name: str | None,
    ) -> tuple[Literal["main", "isolated", "fresh", "named"], str | None]:
        new_mode = session_mode or existing.session_mode
        if new_mode not in {"main", "isolated", "fresh", "named"}:
            raise ValueError(
                "session_mode must be one of: main, isolated, fresh, named"
            )

        if new_mode != "named":
            return new_mode, None

        new_name = session_name if session_name is not None else existing.session_name
        if not new_name or not re.match(r"^[a-zA-Z0-9_-]+$", new_name):
            raise ValueError(
                "session_name is required for session_mode='named' "
                "and must contain only letters, digits, hyphens, and underscores"
            )
        return new_mode, new_name

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
        if self._runner is None or not self._runner.has_agent:
            logger.warning("scheduler.no_agent", job_id=job.job_id)
            return

        address = ChatAddress.from_inbound(
            job.platform,
            job.chat_id,
            chat_type=job.chat_type,
        )

        # Resolve session_id based on session_mode
        if job.session_mode == "isolated":
            session_id = f"{job.session_key}:cron:{job.job_id}"
        elif job.session_mode == "fresh":
            session_id = f"{job.session_key}:cron:{job.job_id}:fire:{job.run_count + 1}"
        elif job.session_mode == "named":
            session_id = f"{job.session_key}:cron:{job.session_name}"
        else:
            if job.created_from_session_id:
                session_id = job.created_from_session_id
            else:
                session_id = job.session_key
                if self._router is not None:
                    session_id = self._router.get_active_session_id(address)

        message_context = MessageContext(
            timestamp=datetime.now(UTC).timestamp(),
            channel=address.channel,
            chat_type=address.target_type,
            chat_id=address.target_id,
            sender_id=job.created_by_user_id or "scheduler",
            sender_display_name=job.created_by_user_id or "scheduler",
            sender_role_tags=("cron",),
            extra_tags=("cron_trigger", job.job_id),
        )

        # Set session context for tool handlers
        ctx_token = current_session.set(
            SessionContext(
                platform=job.platform,
                chat_id=job.chat_id,
                session_id=session_id,
                workspace_id=job.workspace_id,
                chat_address=address,
                user_id=job.created_by_user_id,
                sender_display_name=job.created_by_user_id or "scheduler",
            )
        )
        try:
            await self._do_fire(job, session_id, address, message_context)
        finally:
            current_session.reset(ctx_token)

    async def _do_fire(
        self,
        job: CronJob,
        session_id: str,
        address: ChatAddress,
        message_context: MessageContext,
    ) -> None:
        """The actual agent execution + response delivery."""
        assert self._runner is not None  # guarded by _execute_fire
        result = await self._runner.run(
            user_message=job.prompt,
            session_id=session_id,
            system_prompt=self._system_prompt,
            workspace_id=job.workspace_id,
            message_context=message_context,
            tool_filter=_CRON_TOOL_NAMES,
            source_tag="cron_trigger",
        )

        # Send response via channel
        response_text = result.final_response
        sentinel_action: str | None = None
        sentinel_suppressed = False
        if self._enable_silent_reply and response_text:
            sr = detect_sentinel(response_text)
            if sr.action is not None:
                sentinel_action = sr.action
                response_text = sr.text
                sentinel_suppressed = not bool(response_text)
                logger.debug(
                    "scheduler.sentinel_detected",
                    job_id=job.job_id,
                    action=sr.action,
                    suppressed=not response_text,
                )

        if response_text and self._channels is not None:
            channel = self._channels.get(job.platform)
            if channel is not None:
                message_id = await channel.send_message(
                    address.target_id,
                    OutboundMessage(
                        text=response_text,
                        extra={"chat_address": address.chat_key},
                    ),
                )
                await self._record_delivery(
                    job=job,
                    address=address,
                    session_id=session_id,
                    text=response_text,
                    source="scheduler_cron",
                    delivery_mode="cron_final",
                    message_id=message_id,
                    metadata={
                        "job_id": job.job_id,
                        "session_id": session_id,
                        "session_mode": job.session_mode,
                        "sentinel_action": sentinel_action,
                        "sentinel_suppressed": sentinel_suppressed,
                    },
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
        address = ChatAddress.from_inbound(
            job.platform,
            job.chat_id,
            chat_type=job.chat_type,
        )
        channel = self._channels.get(job.platform)
        if channel is not None:
            try:
                text = f"[Scheduler] {message}"
                message_id = await channel.send_message(
                    address.target_id,
                    OutboundMessage(
                        text=text,
                        extra={"chat_address": address.chat_key},
                    ),
                )
                await self._record_delivery(
                    job=job,
                    address=address,
                    session_id=job.created_from_session_id,
                    text=text,
                    source="scheduler_cron",
                    delivery_mode="cron_error",
                    message_id=message_id,
                    metadata={
                        "job_id": job.job_id,
                        "session_id": job.created_from_session_id,
                        "session_mode": job.session_mode,
                    },
                )
            except Exception:
                logger.exception("scheduler.send_error_failed", job_id=job.job_id)

    async def _record_delivery(
        self,
        *,
        job: CronJob,
        address: ChatAddress,
        session_id: str,
        text: str,
        source: str,
        delivery_mode: str,
        message_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self._message_delivery_store is None:
            return
        try:
            await self._message_delivery_store.record(
                target_chat_address=address.chat_key,
                platform=address.channel,
                target_type=address.target_type,
                target_id=address.target_id,
                source_session_id=session_id,
                source_chat_address=job.created_from_chat_address or address.chat_key,
                source_user_id=job.created_by_user_id,
                source=source,
                delivery_mode=delivery_mode,
                status="sent",
                message_id=message_id,
                text=text,
                metadata=metadata,
            )
        except Exception:
            logger.warning(
                "scheduler.delivery_audit_failed",
                job_id=job.job_id,
                message_id=message_id,
                exc_info=True,
            )

    # ── Validators ────────────────────────────────────────

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

    def _validate_and_get_next_cron(
        self, cron_expression: str | None, *, now: datetime
    ) -> str:
        """Validate a cron expression and return the next fire time as ISO."""
        if not cron_expression:
            raise ValueError("cron_expression is required for mode='cron'")
        if len(cron_expression.split()) != 5:
            raise ValueError(
                "Invalid cron expression: must use standard 5-field syntax"
            )
        try:
            cron = croniter(cron_expression, now)
        except (ValueError, KeyError) as exc:
            raise ValueError(f"Invalid cron expression: {exc}") from exc
        next_dt = cron.get_next(datetime)
        if next_dt <= now:
            raise ValueError("cron expression must resolve to a future time")
        following_dt = cron.get_next(datetime)
        interval_seconds = (following_dt - next_dt).total_seconds()
        if interval_seconds < self._config.min_interval_seconds:
            raise ValueError(
                "cron_expression interval must be >= "
                f"{self._config.min_interval_seconds} seconds"
            )
        return next_dt.isoformat()

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


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _legacy_model_spec(*, provider_id: str = "", model: str = "") -> str:
    """Build a model spec from legacy provider/model split fields."""
    provider_id = provider_id.strip()
    model = model.strip()
    if provider_id and model:
        if model.startswith(f"{provider_id}/"):
            return model
        return f"{provider_id}/{model}"
    return model
