"""Tests for the Phase 0 agent-run log analyzer."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.analyze_agent_runs import analyze, iter_log_records, render


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for rec in records:
            handle.write(json.dumps(rec, ensure_ascii=False) + "\n")


def test_iter_log_records_skips_blank_and_non_json(tmp_path: Path) -> None:
    log = tmp_path / "nahida.log"
    log.write_text(
        json.dumps({"event": "agent_loop.run_completed"}) + "\n\nthis is not json\n",
        encoding="utf-8",
    )
    records = list(iter_log_records(log))
    assert len(records) == 1
    assert records[0]["event"] == "agent_loop.run_completed"


def test_analyze_aggregates_terminal_outcomes_and_anomalies(tmp_path: Path) -> None:
    log = tmp_path / "nahida.log"
    # Mix of: plain no_tool_calls (#21 signal), tool_calls_completed, a protocol
    # anomaly, a max_steps incomplete run, and a legacy entry without terminal_state.
    records = [
        # Run 1: plain answer, no tools. Context comes from the start log.
        {
            "event": "agent_loop.run",
            "trace_id": "t1",
            "provider_name": "openai",
            "provider_default_model": "gpt-x",
            "model_override": "",
        },
        {
            "event": "agent_loop.run_completed",
            "trace_id": "t1",
            "terminal_state": "completed",
            "reason": "no_tool_calls",
            "finish_reason": "stop",
            "protocol_anomaly": "",
        },
        # Run 2: completed after tools.
        {
            "event": "agent_loop.run_completed",
            "trace_id": "t2",
            "terminal_state": "completed",
            "reason": "tool_calls_completed",
            "finish_reason": "stop",
            "protocol_anomaly": "",
        },
        # Run 3: protocol anomaly (provider said tool_calls but parsed none).
        {
            "event": "session_runner.agent_run_done",
            "trace_id": "t3",
            "provider_id": "glm",
            "effective_model": "glm-5",
        },
        {
            "event": "agent_loop.run_completed",
            "trace_id": "t3",
            "terminal_state": "completed",
            "reason": "no_tool_calls",
            "finish_reason": "tool_calls",
            "protocol_anomaly": "tool_finish_without_parsed_calls",
        },
        # Run 4: max_steps.
        {
            "event": "agent_loop.run_completed",
            "trace_id": "t4",
            "terminal_state": "incomplete",
            "reason": "max_steps_reached",
            "finish_reason": "tool_calls",
            "protocol_anomaly": "",
        },
        # Run 5: legacy entry written before Phase 0 (no terminal_state field).
        {
            "event": "agent_loop.run_completed",
            "trace_id": "t5",
            "reason": "no_tool_calls",
            "finish_reason": "stop",
        },
    ]
    _write_jsonl(log, records)

    report = analyze(list(iter_log_records(log)))

    assert report["total_runs"] == 5
    # no_tool_calls (reason-based) = t1, t3, t5 (t5 is legacy, still counted).
    assert report["no_tool_calls"] == 3
    assert report["by_state_reason"]["completed:no_tool_calls"] == 2
    assert report["by_state_reason"]["completed:tool_calls_completed"] == 1
    assert report["by_state_reason"]["incomplete:max_steps_reached"] == 1
    assert report["by_state_reason"]["legacy_unknown:no_tool_calls"] == 1
    # Anomaly breakdown joins provider/model from the session_runner done log.
    assert report["protocol_anomaly_total"] == 1
    assert report["protocol_anomaly_by_provider_model"] == {"glm/glm-5": 1}
    assert "t3" in report["anomaly_trace_ids"]
    # finish_reason distribution.
    assert report["finish_reason_counts"]["stop"] == 3
    assert report["finish_reason_counts"]["tool_calls"] == 2


def test_render_handles_empty_report() -> None:
    text = render(
        {
            "total_runs": 0,
            "by_state_reason": {},
            "no_tool_calls": 0,
            "no_tool_calls_rate": 0.0,
            "protocol_anomaly_total": 0,
            "protocol_anomaly_rate": 0.0,
            "protocol_anomaly_by_provider_model": {},
            "finish_reason_counts": {},
            "anomaly_trace_ids": [],
        }
    )
    assert "no run_completed events found" in text
