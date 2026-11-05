"""A bounded single-writer queue for work derived from committed turns."""

from __future__ import annotations

import asyncio
from collections import deque
from contextvars import Context
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable

import structlog

logger = structlog.get_logger(__name__)


class PostprocessClosed(RuntimeError):
    """Shutdown stopped acceptance; the source conversation is already durable."""


@dataclass(slots=True)
class _Job:
    operation: Callable[[], Awaitable[None]]
    size_bytes: int
    label: str
    done: asyncio.Future[None] | None = None


class PostprocessQueue:
    """Serialize shared memory changes, with backpressure and explicit draining."""

    def __init__(self, *, max_pending: int = 64, max_pending_bytes: int = 8_388_608):
        if max_pending < 1 or max_pending_bytes < 1:
            raise ValueError("Postprocessing limits must be positive")
        self.max_pending = max_pending
        self.max_pending_bytes = max_pending_bytes
        self._items: deque[_Job] = deque()
        self._worker: asyncio.Task[None] | None = None
        self._submission = asyncio.Lock()
        self._changed = asyncio.Condition()
        self._accepting = True
        self._running = 0
        self._bytes = 0
        self._completed = self._failed = self._cancelled = 0

    @property
    def snapshot(self) -> dict[str, int]:
        return dict(
            queued=len(self._items),
            running=self._running,
            pending_bytes=self._bytes,
            completed=self._completed,
            failed=self._failed,
            cancelled=self._cancelled,
        )

    @property
    def closed(self) -> bool:
        return not self._accepting

    def _has_capacity(self, size_bytes: int) -> bool:
        if size_bytes > self.max_pending_bytes:
            return not self._running and not self._items
        return (
            len(self._items) + self._running < self.max_pending
            and self._bytes + size_bytes <= self.max_pending_bytes
        )

    async def submit(
        self,
        operation: Callable[[], Awaitable[None]],
        *,
        size_bytes: int,
        label: str,
    ) -> None:
        """Large jobs drain first and retain foreground backpressure until done."""
        if size_bytes < 0:
            raise ValueError("Postprocessing payload size must not be negative")
        oversized = size_bytes > self.max_pending_bytes
        async with self._submission:
            async with self._changed:
                await self._changed.wait_for(
                    lambda: not self._accepting or self._has_capacity(size_bytes)
                )
                if not self._accepting:
                    raise PostprocessClosed("Memory postprocessing is shutting down")
                done = asyncio.get_running_loop().create_future() if oversized else None
                self._items.append(_Job(operation, size_bytes, label, done))
                self._bytes += size_bytes
                if self._worker is None:
                    # Work carries an explicit identity snapshot, never a turn ContextVar.
                    self._worker = asyncio.create_task(
                        self._drain(),
                        name="memory-postprocess",
                        context=Context(),
                    )
            if done is not None:
                await asyncio.shield(done)

    async def join(self) -> None:
        async with self._changed:
            await self._changed.wait_for(lambda: not self._running and not self._items)

    @asynccontextmanager
    async def mutation(self) -> AsyncIterator[None]:
        """Commit explicit memory edits after older maintenance, before newer jobs."""
        if asyncio.current_task() is self._worker:
            raise RuntimeError(
                "Maintenance must not recursively enter its mutation barrier"
            )
        async with self._submission:
            await self.join()
            yield

    async def close(self, *, timeout: float = 30.0) -> None:
        self._accepting = False
        async with self._changed:
            self._changed.notify_all()
        worker = self._worker
        if worker is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(worker), timeout=timeout)
        except (TimeoutError, asyncio.CancelledError) as exc:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            logger.warning("memory.postprocess_interrupted", **self.snapshot)
            if isinstance(exc, asyncio.CancelledError):
                raise

    async def _drain(self) -> None:
        try:
            while self._items:
                job = self._items.popleft()
                self._running = 1
                try:
                    await job.operation()
                except asyncio.CancelledError:
                    self._cancelled += 1
                    raise
                except Exception:
                    self._failed += 1
                    logger.warning(
                        "memory.postprocess_failed", job=job.label, exc_info=True
                    )
                else:
                    self._completed += 1
                finally:
                    self._running = 0
                    self._bytes -= job.size_bytes
                    if job.done is not None and not job.done.done():
                        job.done.set_result(None)
                    async with self._changed:
                        self._changed.notify_all()
        finally:
            self._cancelled += len(self._items)
            for pending in self._items:
                if pending.done is not None and not pending.done.done():
                    pending.done.set_result(None)
            self._items.clear()
            self._bytes = 0
            self._worker = None
            async with self._changed:
                self._changed.notify_all()
