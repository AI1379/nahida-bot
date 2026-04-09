"""Unit tests for agent observability metrics."""

from __future__ import annotations

import pytest

from nahida_bot.agent.metrics import (
    ContextPruneRecord,
    MetricsCollector,
    ProviderCallRecord,
    ToolCallRecord,
    Trace,
)


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------


class TestTrace:
    def test_trace_id_is_populated(self) -> None:
        trace = Trace()
        assert len(trace.trace_id) == 16

    def test_total_duration_increases(self) -> None:
        trace = Trace()
        # started_at was just set; duration should be near zero.
        assert trace.total_duration_seconds >= 0.0

    def test_separate_traces_have_distinct_ids(self) -> None:
        a = Trace()
        b = Trace()
        assert a.trace_id != b.trace_id

    def test_records_lists_start_empty(self) -> None:
        trace = Trace()
        assert trace.provider_calls == []
        assert trace.tool_calls == []
        assert trace.context_prunes == []


# ---------------------------------------------------------------------------
# MetricsCollector — recording
# ---------------------------------------------------------------------------


class TestMetricsCollectorRecording:
    def test_new_trace_is_tracked(self) -> None:
        collector = MetricsCollector()
        trace = collector.new_trace()
        assert collector.trace_count == 1
        assert trace.trace_id

    def test_record_provider_call(self) -> None:
        collector = MetricsCollector()
        trace = collector.new_trace()
        collector.record_provider_call(
            trace, step=1, latency_seconds=0.5, error_code=None
        )
        assert len(trace.provider_calls) == 1
        rec = trace.provider_calls[0]
        assert isinstance(rec, ProviderCallRecord)
        assert rec.step == 1
        assert rec.latency_seconds == 0.5
        assert rec.error_code is None

    def test_record_provider_call_with_error(self) -> None:
        collector = MetricsCollector()
        trace = collector.new_trace()
        collector.record_provider_call(
            trace,
            step=1,
            latency_seconds=2.0,
            error_code="provider_timeout",
            retryable=True,
        )
        rec = trace.provider_calls[0]
        assert rec.error_code == "provider_timeout"
        assert rec.retryable is True

    def test_record_tool_call_success(self) -> None:
        collector = MetricsCollector()
        trace = collector.new_trace()
        collector.record_tool_call(
            trace, step=1, tool_name="read_file", latency_seconds=0.1
        )
        assert len(trace.tool_calls) == 1
        rec = trace.tool_calls[0]
        assert isinstance(rec, ToolCallRecord)
        assert rec.success is True
        assert rec.tool_name == "read_file"

    def test_record_tool_call_failure(self) -> None:
        collector = MetricsCollector()
        trace = collector.new_trace()
        collector.record_tool_call(
            trace,
            step=2,
            tool_name="exec",
            latency_seconds=0.3,
            success=False,
            error_code="tool_execution_exception",
            retryable=False,
        )
        rec = trace.tool_calls[0]
        assert rec.success is False
        assert rec.error_code == "tool_execution_exception"

    def test_record_context_prune(self) -> None:
        collector = MetricsCollector()
        trace = collector.new_trace()
        collector.record_context_prune(trace, step=1, original_count=20, pruned_count=5)
        assert len(trace.context_prunes) == 1
        rec = trace.context_prunes[0]
        assert isinstance(rec, ContextPruneRecord)
        assert rec.original_count == 20
        assert rec.pruned_count == 5


# ---------------------------------------------------------------------------
# MetricsCollector — aggregate queries
# ---------------------------------------------------------------------------


class TestMetricsCollectorAggregates:
    def test_provider_latency_stats_empty(self) -> None:
        collector = MetricsCollector()
        stats = collector.provider_latency_stats()
        assert stats["count"] == 0.0

    def test_provider_latency_stats(self) -> None:
        collector = MetricsCollector()
        trace = collector.new_trace()
        collector.record_provider_call(trace, step=1, latency_seconds=1.0)
        collector.record_provider_call(trace, step=2, latency_seconds=3.0)

        stats = collector.provider_latency_stats()
        assert stats["count"] == 2.0
        assert stats["total"] == 4.0
        assert stats["min"] == 1.0
        assert stats["max"] == 3.0
        assert stats["avg"] == 2.0

    def test_tool_success_rate_no_calls(self) -> None:
        collector = MetricsCollector()
        assert collector.tool_success_rate() == 1.0

    def test_tool_success_rate_mixed(self) -> None:
        collector = MetricsCollector()
        trace = collector.new_trace()
        collector.record_tool_call(trace, step=1, tool_name="a", latency_seconds=0.1)
        collector.record_tool_call(
            trace,
            step=2,
            tool_name="b",
            latency_seconds=0.2,
            success=False,
            error_code="fail",
        )
        collector.record_tool_call(trace, step=3, tool_name="c", latency_seconds=0.1)
        assert collector.tool_success_rate() == pytest.approx(2.0 / 3.0)

    def test_provider_error_rate_no_calls(self) -> None:
        collector = MetricsCollector()
        assert collector.provider_error_rate() == 0.0

    def test_provider_error_rate(self) -> None:
        collector = MetricsCollector()
        trace = collector.new_trace()
        collector.record_provider_call(trace, step=1, latency_seconds=1.0)
        collector.record_provider_call(
            trace,
            step=2,
            latency_seconds=2.0,
            error_code="provider_timeout",
        )
        assert collector.provider_error_rate() == pytest.approx(0.5)

    def test_multiple_traces_are_aggregated(self) -> None:
        collector = MetricsCollector()
        t1 = collector.new_trace()
        t2 = collector.new_trace()
        collector.record_tool_call(t1, step=1, tool_name="a", latency_seconds=0.1)
        collector.record_tool_call(
            t2,
            step=1,
            tool_name="b",
            latency_seconds=0.2,
            success=False,
            error_code="err",
        )
        assert collector.tool_success_rate() == pytest.approx(0.5)
        assert collector.trace_count == 2


# ---------------------------------------------------------------------------
# MetricsCollector — max_traces ring buffer
# ---------------------------------------------------------------------------


class TestMetricsCollectorRingBuffer:
    def test_default_max_traces_is_100(self) -> None:
        collector = MetricsCollector()
        assert collector._max_traces == 100

    def test_traces_evicted_beyond_max(self) -> None:
        collector = MetricsCollector(max_traces=3)
        t1 = collector.new_trace()
        t2 = collector.new_trace()
        t3 = collector.new_trace()
        t4 = collector.new_trace()

        assert collector.trace_count == 3
        # t1 should have been evicted; t2, t3, t4 remain.
        active_ids = {t.trace_id for t in collector._traces}
        assert t1.trace_id not in active_ids
        assert t2.trace_id in active_ids
        assert t3.trace_id in active_ids
        assert t4.trace_id in active_ids

    def test_zero_max_traces_means_unlimited(self) -> None:
        collector = MetricsCollector(max_traces=0)
        for _ in range(200):
            collector.new_trace()
        assert collector.trace_count == 200
