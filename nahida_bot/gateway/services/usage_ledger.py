"""In-memory usage ledger for token tracking.

Records per-request token usage and provides aggregate totals.
Will be backed by SQLite in a later phase.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import structlog

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class UsageEvent:
    timestamp: str
    session_id: str
    source_tag: str
    provider_id: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    cache_creation_tokens: int = 0
    estimated_cost: float | None = None


@dataclass(slots=True)
class UsageTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    cache_creation_tokens: int = 0
    estimated_cost: float | None = None
    event_count: int = 0


class InMemoryUsageLedger:
    """Thread-safe in-memory usage ledger.

    Stores a bounded number of recent events and maintains running totals.
    Not suitable for long-running persistence — events are lost on restart.
    """

    def __init__(self, *, max_events: int = 10000) -> None:
        self._events: list[UsageEvent] = []
        self._totals = UsageTotals()
        self._max_events = max_events

    def record(
        self,
        *,
        session_id: str = "",
        source_tag: str = "",
        provider_id: str = "",
        model: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_tokens: int = 0,
        reasoning_tokens: int = 0,
        cache_creation_tokens: int = 0,
        estimated_cost: float | None = None,
    ) -> None:
        """Record a usage event."""
        event = UsageEvent(
            timestamp=datetime.now(UTC).isoformat(),
            session_id=session_id,
            source_tag=source_tag,
            provider_id=provider_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            reasoning_tokens=reasoning_tokens,
            cache_creation_tokens=cache_creation_tokens,
            estimated_cost=estimated_cost,
        )

        self._events.append(event)
        if len(self._events) > self._max_events:
            dropped = len(self._events) - self._max_events
            self._events = self._events[-self._max_events :]
            logger.debug("usage_ledger.trimmed", dropped=dropped)

        # Update running totals
        t = self._totals
        t.input_tokens += input_tokens
        t.output_tokens += output_tokens
        t.cached_tokens += cached_tokens
        t.reasoning_tokens += reasoning_tokens
        t.cache_creation_tokens += cache_creation_tokens
        if estimated_cost is not None:
            t.estimated_cost = (t.estimated_cost or 0) + estimated_cost
        t.event_count += 1

    def get_totals(self) -> UsageTotals:
        """Return current aggregate totals."""
        return UsageTotals(
            input_tokens=self._totals.input_tokens,
            output_tokens=self._totals.output_tokens,
            cached_tokens=self._totals.cached_tokens,
            reasoning_tokens=self._totals.reasoning_tokens,
            cache_creation_tokens=self._totals.cache_creation_tokens,
            estimated_cost=self._totals.estimated_cost,
            event_count=self._totals.event_count,
        )

    def get_recent(self, *, limit: int = 100) -> list[UsageEvent]:
        """Return recent events, newest last."""
        return self._events[-limit:]
