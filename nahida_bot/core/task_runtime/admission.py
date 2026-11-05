"""Bounded, cancellation-safe admission with fair owner queues."""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Literal

ServiceClass = Literal["interactive", "background", "maintenance"]
_CLASSES: tuple[ServiceClass, ...] = (
    "interactive",
    "background",
    "interactive",
    "maintenance",
    "interactive",
    "background",
    "interactive",
)


class AdmissionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(eq=False, slots=True)
class _Ticket:
    owner: str
    service_class: ServiceClass
    ready: asyncio.Future[None]
    granted: bool = False


class ExecutionAdmission:
    """Allocate slots synchronously; hold each lease through executor cleanup."""

    def __init__(self, capacity: int, max_queued: int, max_queued_per_owner: int):
        if min(capacity, max_queued, max_queued_per_owner) < 1:
            raise ValueError("Admission limits must be positive")
        self.capacity = capacity
        self.max_queued = max_queued
        self.max_queued_per_owner = max_queued_per_owner
        self._active = 0
        self._quarantined: set[object] = set()
        self._queues: dict[ServiceClass, dict[str, deque[_Ticket]]] = {
            service_class: {} for service_class in set(_CLASSES)
        }
        self._cursor = 0

    @property
    def snapshot(self) -> dict[str, int]:
        return {
            "capacity": self.capacity,
            "running": self._active - len(self._quarantined),
            "quarantined": len(self._quarantined),
            "queued": sum(
                len(q) for owners in self._queues.values() for q in owners.values()
            ),
        }

    @asynccontextmanager
    async def slot(
        self, owner: str, service_class: ServiceClass, timeout: float
    ) -> AsyncIterator[object]:
        """Remove cancelled waiters, including a cancellation racing with grant."""
        if service_class not in self._queues:
            raise ValueError("Unknown execution service class")
        owner_depth = sum(
            len(owners.get(owner, ())) for owners in self._queues.values()
        )
        if (
            self.snapshot["queued"] >= self.max_queued
            or owner_depth >= self.max_queued_per_owner
        ):
            raise AdmissionError(
                "queue_full", "Execution queue is full; try again later"
            )
        ticket = _Ticket(
            owner, service_class, asyncio.get_running_loop().create_future()
        )
        self._queues[service_class].setdefault(owner, deque()).append(ticket)
        self._dispatch()
        try:
            if not ticket.granted:
                try:
                    await asyncio.wait_for(asyncio.shield(ticket.ready), timeout)
                except TimeoutError:
                    raise AdmissionError(
                        "queue_timeout",
                        "Execution did not start within its queue budget",
                    ) from None
            yield ticket
        finally:
            if ticket.granted and ticket not in self._quarantined:
                self._active -= 1
            elif not ticket.granted:
                self._remove(ticket)
                ticket.ready.cancel()
            self._dispatch()

    def quarantine(self, lease: object) -> None:
        """Keep an uncertain executor's slot unavailable until service restart."""
        self._quarantined.add(lease)

    def is_quarantined(self, lease: object) -> bool:
        return lease in self._quarantined

    def _remove(self, ticket: _Ticket) -> None:
        owners = self._queues[ticket.service_class]
        queue = owners.get(ticket.owner)
        if queue is not None:
            queue.remove(ticket)
            if not queue:
                del owners[ticket.owner]

    def _dispatch(self) -> None:
        while self._active < self.capacity:
            ticket = self._next()
            if ticket is None:
                return
            ticket.granted = True
            self._active += 1
            ticket.ready.set_result(None)

    def _next(self) -> _Ticket | None:
        for _ in _CLASSES:
            service_class = _CLASSES[self._cursor]
            self._cursor = (self._cursor + 1) % len(_CLASSES)
            owners = self._queues[service_class]
            if owners:
                owner = next(iter(owners))
                queue = owners.pop(owner)
                ticket = queue.popleft()
                if queue:
                    owners[owner] = queue
                return ticket
        return None
