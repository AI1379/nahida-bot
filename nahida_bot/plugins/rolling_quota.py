"""Shared in-memory rolling-window quota reservations for plugins."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class QuotaReservation:
    """A reservation for one or more units in a rolling quota window."""

    reservation_id: str
    count: int


class RollingQuotaExceeded(Exception):
    """Quota reservation was rejected because the window is full."""

    def __init__(
        self,
        *,
        used: int,
        limit: int,
        requested: int,
        retry_after: float,
    ) -> None:
        self.used = used
        self.limit = limit
        self.requested = requested
        self.retry_after = retry_after
        super().__init__(
            f"rolling quota exceeded ({used}/{limit}, requested {requested})"
        )


class RollingQuotaLimiter:
    """Atomically reserve and release units in a rolling time window."""

    def __init__(self, limit: int, *, window_seconds: float = 24 * 60 * 60) -> None:
        self._limit = limit
        self._window_seconds = window_seconds
        self._events: deque[tuple[float, str]] = deque()
        self._lock = asyncio.Lock()
        self._next_id = 0

    async def reserve(self, count: int = 1) -> QuotaReservation | None:
        """Reserve *count* units, or return ``None`` when quota is disabled."""
        if count <= 0:
            raise ValueError("Quota reservation count must be positive")
        if self._limit <= 0:
            return None

        now = time.time()
        async with self._lock:
            self._prune(now)
            used = len(self._events)
            if count > self._limit - used:
                units_to_free = max(1, count - (self._limit - used))
                raise RollingQuotaExceeded(
                    used=used,
                    limit=self._limit,
                    requested=count,
                    retry_after=self.retry_after_seconds(
                        now,
                        units_to_free=units_to_free,
                    ),
                )
            self._next_id += 1
            reservation_id = str(self._next_id)
            for _ in range(count):
                self._events.append((now, reservation_id))
            return QuotaReservation(reservation_id=reservation_id, count=count)

    async def release(
        self,
        reservation: QuotaReservation | None,
        *,
        count: int | None = None,
    ) -> None:
        """Release all or part of a prior reservation."""
        if reservation is None:
            return
        release_count = reservation.count if count is None else max(0, count)
        if release_count <= 0:
            return

        async with self._lock:
            remaining: deque[tuple[float, str]] = deque()
            removed = 0
            for event in self._events:
                if event[1] == reservation.reservation_id and removed < release_count:
                    removed += 1
                    continue
                remaining.append(event)
            self._events = remaining

    def retry_after_seconds(
        self,
        now: float | None = None,
        *,
        units_to_free: int = 1,
    ) -> float:
        """Return seconds until enough current units expire."""
        current = time.time() if now is None else now
        if not self._events:
            return float(self._window_seconds)
        event_index = min(max(1, units_to_free), len(self._events)) - 1
        return max(
            0.0,
            self._events[event_index][0] + self._window_seconds - current,
        )

    def _prune(self, now: float) -> None:
        cutoff = now - self._window_seconds
        while self._events and self._events[0][0] <= cutoff:
            self._events.popleft()
