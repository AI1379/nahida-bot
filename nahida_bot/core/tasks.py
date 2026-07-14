"""Centralized asyncio task lifecycle manager.

Provides a unified way to create, track, cancel, and observe background
tasks across the application and plugins.  Every managed task is named,
owned (by a plugin id or a core module), and automatically cleaned up
when its owner is disabled or the application shuts down.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable, Coroutine, Literal

import structlog

logger = structlog.get_logger(__name__)

# Type aliases
OnErrorCallback = Callable[[str, Exception], Awaitable[None]]


# ── Public data types ──────────────────────────────────────


@dataclass(slots=True, frozen=True)
class TaskInfo:
    """Immutable snapshot of a managed task's metadata."""

    name: str
    owner: str
    status: Literal["running", "cancelled", "done", "failed"]
    created_at: datetime
    kind: Literal["oneshot", "interval", "reconnecting"]
    error: str | None = None


# ── Internal bookkeeping ───────────────────────────────────


@dataclass(slots=True)
class _ManagedTask:
    """Internal tracking record for one task."""

    name: str
    owner: str
    task: asyncio.Task[Any]
    created_at: datetime
    kind: str  # oneshot | interval | reconnecting
    on_error: OnErrorCallback | None
    # Mutable status — updated via done_callback
    status: Literal["running", "cancelled", "done", "failed"] = "running"
    error: str | None = None


# ── TaskManager ────────────────────────────────────────────


class TaskManager:
    """Centralized asyncio task lifecycle manager.

    Every task has a unique ``{owner}:{name}`` key.  Owners are typically
    plugin ids or ``"core.<module>"`` strings.  When a plugin is disabled,
    :meth:`cancel_by_owner_and_await` cancels all its tasks.

    Usage::

        tm = TaskManager()
        tm.spawn("polling", poll_loop(), owner="plugin.telegram")
        tm.spawn_interval("heartbeat", send_ping, owner="core.gateway",
                          interval_seconds=15)
        # On shutdown:
        await tm.shutdown(timeout=10)
    """

    def __init__(self) -> None:
        self._tasks: dict[str, _ManagedTask] = {}
        self._by_owner: dict[str, set[str]] = defaultdict(set)
        self._completed: dict[str, TaskInfo] = {}
        self._logger = structlog.get_logger("task_manager")

    # ── Key helpers ───────────────────────────────────

    @staticmethod
    def _key(owner: str, name: str) -> str:
        return f"{owner}:{name}"

    # ── Spawning ──────────────────────────────────────

    def spawn(
        self,
        name: str,
        coro: Coroutine[Any, Any, Any],
        *,
        owner: str,
        kind: Literal["oneshot", "interval", "reconnecting"] = "oneshot",
        on_error: OnErrorCallback | None = None,
    ) -> asyncio.Task[Any]:
        """Register and start a named background task.

        Raises:
            ValueError: If a task with the same owner+name already exists.
        """
        key = self._key(owner, name)
        existing = self._tasks.get(key)
        if existing is not None and existing.task.done():
            self._finalize_task(key, existing.task)
            existing = self._tasks.get(key)
        if existing is not None:
            coro.close()
            raise ValueError(f"Task '{name}' already exists for owner '{owner}'")

        managed = _ManagedTask(
            name=name,
            owner=owner,
            task=None,  # type: ignore[arg-type] — set below
            created_at=datetime.now(UTC),
            kind=kind,
            on_error=on_error,
        )

        wrapped = self._run_tracked(coro, managed)
        task = asyncio.create_task(wrapped, name=f"tm:{key}")
        managed.task = task

        self._tasks[key] = managed
        self._by_owner[owner].add(key)
        self._completed.pop(key, None)

        task.add_done_callback(self._make_done_callback(key))

        self._logger.debug(
            "task_manager.spawned",
            task_name=name,
            owner=owner,
            kind=kind,
        )
        return task

    def spawn_interval(
        self,
        name: str,
        func: Callable[[], Awaitable[None]],
        *,
        owner: str,
        interval_seconds: float,
        initial_delay: float = 0.0,
        on_error: OnErrorCallback | None = None,
    ) -> asyncio.Task[Any]:
        """Spawn a task that calls *func* every *interval_seconds*."""

        async def _loop() -> None:
            if initial_delay > 0:
                await asyncio.sleep(initial_delay)
            while True:
                await func()
                await asyncio.sleep(interval_seconds)

        return self.spawn(
            name, _loop(), owner=owner, kind="interval", on_error=on_error
        )

    def spawn_reconnecting(
        self,
        name: str,
        factory: Callable[[], Coroutine[Any, Any, None]],
        *,
        owner: str,
        initial_delay: float = 1.0,
        max_delay: float = 60.0,
        backoff_factor: float = 2.0,
        on_error: OnErrorCallback | None = None,
    ) -> asyncio.Task[Any]:
        """Spawn a reconnecting loop around a coroutine factory.

        The *factory* is called repeatedly.  On success it is called again
        immediately; on failure the call is retried after an exponential
        backoff (starting at *initial_delay*, capped at *max_delay*).
        """

        async def _reconnect_loop() -> None:
            delay = initial_delay
            while True:
                try:
                    await factory()
                    delay = initial_delay  # reset on clean exit
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await self._handle_task_error(
                        name=name,
                        owner=owner,
                        kind="reconnecting",
                        on_error=on_error,
                        exc=exc,
                    )
                await asyncio.sleep(delay)
                delay = min(delay * backoff_factor, max_delay)

        return self.spawn(
            name,
            _reconnect_loop(),
            owner=owner,
            kind="reconnecting",
            on_error=on_error,
        )

    # ── Cancellation ──────────────────────────────────

    def cancel(self, name: str) -> bool:
        """Cancel a named task.  *name* is the full ``owner:name`` key.

        Returns ``True`` if the task was found and cancelled.
        """
        managed = self._tasks.get(name)
        if managed is None:
            return False
        if managed.task.done():
            self._finalize_task(name, managed.task)
            return False
        managed.task.cancel()
        return True

    async def cancel_and_await(self, name: str, timeout: float = 5.0) -> bool:
        """Cancel a task and wait for it to finish."""
        managed = self._tasks.get(name)
        if managed is None:
            return False
        if managed.task.done():
            self._finalize_task(name, managed.task)
            return True
        managed.task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(managed.task), timeout=timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        return True

    def cancel_by_owner(self, owner: str) -> list[str]:
        """Cancel all tasks belonging to *owner*.  Returns cancelled keys."""
        keys = list(self._by_owner.get(owner, set()))
        cancelled: list[str] = []
        for key in keys:
            managed = self._tasks.get(key)
            if managed is not None and not managed.task.done():
                managed.task.cancel()
                cancelled.append(key)
            elif managed is not None:
                self._finalize_task(key, managed.task)
        return cancelled

    async def cancel_by_owner_and_await(
        self, owner: str, timeout: float = 5.0
    ) -> list[str]:
        """Cancel and await all tasks for *owner*."""
        keys = list(self._by_owner.get(owner, set()))
        tasks_to_await: list[asyncio.Task[Any]] = []
        cancelled: list[str] = []
        for key in keys:
            managed = self._tasks.get(key)
            if managed is not None:
                if managed.task.done():
                    self._finalize_task(key, managed.task)
                else:
                    managed.task.cancel()
                    tasks_to_await.append(managed.task)
                    cancelled.append(key)
        if tasks_to_await:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks_to_await, return_exceptions=True),
                    timeout=timeout,
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        return cancelled

    async def cancel_all_and_await(self, timeout: float = 10.0) -> list[str]:
        """Cancel and await all managed tasks."""
        all_keys = list(self._tasks.keys())
        tasks_to_await: list[asyncio.Task[Any]] = []
        cancelled: list[str] = []
        for key in all_keys:
            managed = self._tasks[key]
            if managed.task.done():
                self._finalize_task(key, managed.task)
            else:
                managed.task.cancel()
                tasks_to_await.append(managed.task)
                cancelled.append(key)
        if tasks_to_await:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks_to_await, return_exceptions=True),
                    timeout=timeout,
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        return cancelled

    # ── Query ─────────────────────────────────────────

    def list_tasks(self, *, owner: str | None = None) -> list[TaskInfo]:
        """Return snapshots of managed tasks, optionally filtered by *owner*."""
        results: list[TaskInfo] = []
        for managed in self._tasks.values():
            if owner is not None and managed.owner != owner:
                continue
            results.append(self._to_info(managed))
        for info in self._completed.values():
            if owner is not None and info.owner != owner:
                continue
            results.append(info)
        return results

    def get_task(self, name: str) -> TaskInfo | None:
        """Return a snapshot of one task by its ``owner:name`` key."""
        managed = self._tasks.get(name)
        if managed is not None:
            return self._to_info(managed)
        return self._completed.get(name)

    # ── Lifecycle ─────────────────────────────────────

    async def shutdown(self, timeout: float = 10.0) -> None:
        """Graceful shutdown: cancel all tasks and await them."""
        self._logger.info("task_manager.shutting_down")
        cancelled = await self.cancel_all_and_await(timeout=timeout)
        if cancelled:
            self._logger.info(
                "task_manager.shutdown_complete",
                cancelled_count=len(cancelled),
            )
        else:
            self._logger.info("task_manager.shutdown_complete")

    # ── Internal helpers ──────────────────────────────

    async def _run_tracked(
        self,
        coro: Coroutine[Any, Any, Any],
        managed: _ManagedTask,
    ) -> None:
        """Wrap a coroutine with error handling and status tracking."""
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._handle_task_error(
                name=managed.name,
                owner=managed.owner,
                kind=managed.kind,
                on_error=managed.on_error,
                exc=exc,
            )
            raise

    async def _handle_task_error(
        self,
        *,
        name: str,
        owner: str,
        kind: str,
        on_error: OnErrorCallback | None,
        exc: Exception,
    ) -> None:
        """Log one task error and invoke the optional error callback."""
        self._logger.exception(
            "task_manager.task_failed",
            task_name=name,
            owner=owner,
            kind=kind,
        )
        if on_error is None:
            return
        try:
            await on_error(name, exc)
        except Exception:
            self._logger.exception(
                "task_manager.error_callback_failed",
                task_name=name,
            )

    def _make_done_callback(self, key: str) -> Callable[[asyncio.Task[Any]], None]:
        """Create a done_callback that updates the managed task's status."""

        def _on_done(task: asyncio.Task[Any]) -> None:
            self._finalize_task(key, task)

        return _on_done

    def _finalize_task(self, key: str, task: asyncio.Task[Any]) -> None:
        """Move a finished task from the active registry to completed snapshots."""
        managed = self._tasks.get(key)
        if managed is None or managed.task is not task:
            return

        if not task.done():
            return
        if task.cancelled():
            managed.status = "cancelled"
        else:
            exc = task.exception()
            if exc is not None:
                managed.status = "failed"
                managed.error = str(exc)
            else:
                managed.status = "done"

        self._completed[key] = self._to_info(managed)
        self._tasks.pop(key, None)
        owner_keys = self._by_owner.get(managed.owner)
        if owner_keys is not None:
            owner_keys.discard(key)
            if not owner_keys:
                self._by_owner.pop(managed.owner, None)

        self._logger.debug(
            "task_manager.task_done",
            task_name=managed.name,
            owner=managed.owner,
            status=managed.status,
        )

    @staticmethod
    def _to_info(managed: _ManagedTask) -> TaskInfo:
        return TaskInfo(
            name=managed.name,
            owner=managed.owner,
            status=managed.status,
            created_at=managed.created_at,
            kind=managed.kind,  # type: ignore[arg-type]
            error=managed.error,
        )
