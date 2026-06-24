"""Aggregate Phase 0 agent-loop telemetry from the JSONL log.

Parses ``data/logs/nahida.log`` (structlog JSON Lines, enabled by the agent-loop
repair Phase 0) and reports:

- the terminal-outcome distribution (``state:reason`` → count),
- the #21 signal — ``completed:no_tool_calls`` rate (runs that ended without any
  tool call, the candidate pool for unverified-completion),
- provider tool-protocol anomalies (``finish_reason=tool_calls`` but nothing
  parsed), broken down by provider/model,
- the ``finish_reason`` distribution.

Use this to baseline #21/#24 *before* enabling Phase 2 enforcement
(``no_tool_calls → unverified``) and Phase 3 receipt gating — those risk false
positives on plain Q&A, and their thresholds depend on rates only real traffic
gives you.

Usage::

    python -m scripts.analyze_agent_runs
    python -m scripts.analyze_agent_runs --log data/logs/nahida.log --json

Note: the agent loop logs ``agent_loop.run_completed`` for the ``completed`` /
``incomplete`` / ``failed`` exit paths but **not** for graceful ``cancelled``
(stop_event) exits. Cancelled runs therefore do not appear here; query the
in-process ``MetricsCollector.terminal_outcome_counts()`` for live cancelled
counts.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

DEFAULT_LOG = Path("data/logs/nahida.log")

# structlog event names this tool joins on.
RUN_COMPLETED_EVENT = "agent_loop.run_completed"
RUN_START_EVENT = "agent_loop.run"
SESSION_DONE_EVENT = "session_runner.agent_run_done"


def iter_log_records(path: Path) -> Iterator[dict[str, Any]]:
    """Yield parsed JSON records, skipping blank or non-JSON lines."""
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                # Non-JSON lines (e.g. a stray console-rendered entry) are skipped.
                continue
            if isinstance(record, dict):
                yield record


def _model_of(ctx: dict[str, Any]) -> str:
    for key in (
        "effective_model",
        "selected_model",
        "model_override",
        "provider_default_model",
    ):
        value = ctx.get(key)
        if value:
            return str(value)
    return "?"


def _provider_of(ctx: dict[str, Any]) -> str:
    return str(ctx.get("provider_name") or ctx.get("provider_id") or "?")


def analyze(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate terminal-outcome telemetry from parsed log records."""
    # Join provider/model context onto each trace_id from the start / done logs.
    trace_context: dict[str, dict[str, Any]] = {}
    context_events = (RUN_START_EVENT, SESSION_DONE_EVENT)
    for rec in records:
        if rec.get("event") not in context_events:
            continue
        tid = rec.get("trace_id")
        if not tid:
            continue
        ctx = trace_context.setdefault(tid, {"trace_id": tid})
        for key in (
            "provider_name",
            "provider_id",
            "provider_default_model",
            "model_override",
            "effective_model",
            "selected_model",
        ):
            value = rec.get(key)
            if value:
                ctx.setdefault(key, value)

    # One outcome per run_completed event.
    outcomes: list[dict[str, Any]] = []
    for rec in records:
        if rec.get("event") != RUN_COMPLETED_EVENT:
            continue
        tid = rec.get("trace_id") or ""
        ctx = trace_context.get(tid, {})
        state = rec.get("terminal_state") or "legacy_unknown"
        reason = rec.get("reason") or "unknown"
        outcomes.append(
            {
                "trace_id": tid,
                "terminal_state": state,
                "reason": reason,
                "protocol_anomaly": rec.get("protocol_anomaly") or "",
                "finish_reason": rec.get("finish_reason") or "",
                "provider": _provider_of(ctx),
                "model": _model_of(ctx),
            }
        )

    total = len(outcomes)
    by_state_reason = Counter(f"{o['terminal_state']}:{o['reason']}" for o in outcomes)
    # The #21 signal: runs that ended without calling a tool. Keyed on ``reason``
    # rather than ``terminal_state`` so pre-Phase-0 log lines (which lack
    # terminal_state but still carry reason="no_tool_calls") are counted too;
    # in a clean Phase-0 log this equals ``completed:no_tool_calls`` exactly.
    no_tool_calls = sum(1 for o in outcomes if o["reason"] == "no_tool_calls")
    anomalies = [o for o in outcomes if o["protocol_anomaly"]]
    anomaly_by_provider_model = Counter(
        f"{o['provider']}/{o['model']}" for o in anomalies
    )
    finish_reasons = Counter(o["finish_reason"] or "(none)" for o in outcomes)

    def rate(n: int) -> float:
        return (n / total) if total else 0.0

    return {
        "total_runs": total,
        "by_state_reason": dict(by_state_reason.most_common()),
        "no_tool_calls": no_tool_calls,
        "no_tool_calls_rate": rate(no_tool_calls),
        "protocol_anomaly_total": len(anomalies),
        "protocol_anomaly_rate": rate(len(anomalies)),
        "protocol_anomaly_by_provider_model": dict(
            anomaly_by_provider_model.most_common()
        ),
        "finish_reason_counts": dict(finish_reasons.most_common()),
        "anomaly_trace_ids": [o["trace_id"] for o in anomalies[:20]],
    }


def _pct(n: int, total: int) -> str:
    return f"{(n / total * 100):5.1f}%" if total else "   n/a"


def render(report: dict[str, Any]) -> str:
    """Human-readable text report."""
    total = report["total_runs"]
    lines: list[str] = [
        f"Agent-loop Phase 0 telemetry  ({total} runs)",
        "",
        "Terminal outcomes (state:reason):",
    ]
    if not total:
        lines.append("  (no run_completed events found — is file logging enabled?)")
    for key, count in report["by_state_reason"].items():
        lines.append(f"  {count:>5}  {_pct(count, total)}  {key}")

    nc = report["no_tool_calls"]
    pa = report["protocol_anomaly_total"]
    lines.extend(
        [
            "",
            f"#21 signal — completed:no_tool_calls : {nc} ({_pct(nc, total)})",
            f"Provider tool-protocol anomalies    : {pa} ({_pct(pa, total)})",
        ]
    )
    for provider_model, count in report["protocol_anomaly_by_provider_model"].items():
        lines.append(f"    {count:>4}  {provider_model}")
    if report["anomaly_trace_ids"]:
        lines.append(
            "    sample trace_ids: " + ", ".join(report["anomaly_trace_ids"][:8])
        )

    lines.extend(["", "finish_reason distribution:"])
    for finish, count in report["finish_reason_counts"].items():
        lines.append(f"  {count:>5}  {_pct(count, total)}  {finish}")

    lines.append("")
    lines.append(
        "Note: cancelled (stop_event) runs are not in run_completed; query the"
        "\n      in-process MetricsCollector.terminal_outcome_counts() for those."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate agent-loop Phase 0 telemetry from the JSONL log."
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=DEFAULT_LOG,
        help=f"Path to the JSONL log file (default: {DEFAULT_LOG}).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the report as JSON instead of human-readable text.",
    )
    args = parser.parse_args(argv)

    if not args.log.exists():
        print(f"log file not found: {args.log}", file=sys.stderr)
        return 2

    report = analyze(list(iter_log_records(args.log)))
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
