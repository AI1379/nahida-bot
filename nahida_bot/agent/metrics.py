"""Minimal observability metrics for the agent loop.

Tracks provider latency, tool call success rates, context pruning, and
per-run trace linkage.  All accumulators are simple integer / float counters
safe for single-threaded async code.  A :class:`Trace` is created per
``AgentLoop.run`` invocation and carries a ``trace_id`` that links every
recorded event back to that invocation.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class ProviderCallRecord:
    """Snapshot of a single provider call."""

    trace_id: str
    step: int
    latency_seconds: float
    error_code: str | None = None
    retryable: bool = False


@dataclass(slots=True, frozen=True)
class ToolCallRecord:
    """Snapshot of a single tool execution."""

    trace_id: str
    step: int
    tool_name: str
    latency_seconds: float
    success: bool = True
    error_code: str | None = None
    retryable: bool = False


@dataclass(slots=True, frozen=True)
class ContextPruneRecord:
    """Snapshot of a context window pruning event."""

    trace_id: str
    step: int
    original_count: int
    pruned_count: int


# ---------------------------------------------------------------------------
# Trace – one per AgentLoop.run() invocation
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Trace:
    """Accumulator for a single agent loop run."""

    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    started_at: float = field(default_factory=time.monotonic)

    provider_calls: list[ProviderCallRecord] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    context_prunes: list[ContextPruneRecord] = field(default_factory=list)

    @property
    def total_duration_seconds(self) -> float:
        return time.monotonic() - self.started_at


# ---------------------------------------------------------------------------
# MetricsCollector – application-wide accumulator
# ---------------------------------------------------------------------------


class MetricsCollector:
    """Collects and aggregates metrics across agent loop runs.

    Args:
        max_traces: Maximum number of completed traces to retain.
            Oldest traces are evicted when the limit is exceeded.
            Defaults to 100. Set to 0 for unlimited growth.
    """

    def __init__(self, *, max_traces: int = 100) -> None:
        self._max_traces = max_traces
        self._traces: list[Trace] = []

    def new_trace(self) -> Trace:
        trace = Trace()
        self._traces.append(trace)
        if self._max_traces > 0 and len(self._traces) > self._max_traces:
            self._traces = self._traces[-self._max_traces :]
        return trace

    # -- record helpers (called by AgentLoop) -----------------------------

    def record_provider_call(
        self,
        trace: Trace,
        *,
        step: int,
        latency_seconds: float,
        error_code: str | None = None,
        retryable: bool = False,
    ) -> None:
        trace.provider_calls.append(
            ProviderCallRecord(
                trace_id=trace.trace_id,
                step=step,
                latency_seconds=latency_seconds,
                error_code=error_code,
                retryable=retryable,
            )
        )

    def record_tool_call(
        self,
        trace: Trace,
        *,
        step: int,
        tool_name: str,
        latency_seconds: float,
        success: bool = True,
        error_code: str | None = None,
        retryable: bool = False,
    ) -> None:
        trace.tool_calls.append(
            ToolCallRecord(
                trace_id=trace.trace_id,
                step=step,
                tool_name=tool_name,
                latency_seconds=latency_seconds,
                success=success,
                error_code=error_code,
                retryable=retryable,
            )
        )

    def record_context_prune(
        self,
        trace: Trace,
        *,
        step: int,
        original_count: int,
        pruned_count: int,
    ) -> None:
        trace.context_prunes.append(
            ContextPruneRecord(
                trace_id=trace.trace_id,
                step=step,
                original_count=original_count,
                pruned_count=pruned_count,
            )
        )

    # -- aggregate queries ------------------------------------------------

    @property
    def trace_count(self) -> int:
        return len(self._traces)

    def provider_latency_stats(self) -> dict[str, float]:
        """Return (count, total, min, max, avg) for provider call latency."""
        latencies = [
            rec.latency_seconds
            for trace in self._traces
            for rec in trace.provider_calls
        ]
        return self._compute_stats(latencies)

    def tool_success_rate(self) -> float:
        """Return fraction of tool calls that succeeded (0.0 – 1.0)."""
        total = sum(len(t.tool_calls) for t in self._traces)
        if total == 0:
            return 1.0
        succeeded = sum(1 for t in self._traces for rec in t.tool_calls if rec.success)
        return succeeded / total

    def provider_error_rate(self) -> float:
        """Return fraction of provider calls that errored (0.0 – 1.0)."""
        total = sum(len(t.provider_calls) for t in self._traces)
        if total == 0:
            return 0.0
        errored = sum(
            1 for t in self._traces for rec in t.provider_calls if rec.error_code
        )
        return errored / total

    @staticmethod
    def _compute_stats(values: list[float]) -> dict[str, float]:
        if not values:
            return {"count": 0.0, "total": 0.0, "min": 0.0, "max": 0.0, "avg": 0.0}
        return {
            "count": float(len(values)),
            "total": sum(values),
            "min": min(values),
            "max": max(values),
            "avg": sum(values) / len(values),
        }
