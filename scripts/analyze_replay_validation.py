"""Validate the agent-loop repair (#21 / #24) against real traffic.

Goes beyond ``analyze_agent_runs.py`` (Phase-0 terminal outcomes only) by joining
the cross-turn **transcript-replay** telemetry onto each run and correlating the
#21 signal (``reason=no_tool_calls``) with **context length** and with whether
replay fired. The core hypothesis under test ([[agent-loop-repair-progress]],
[[agent-loop-21-root-cause-harness-not-model]]):

    #21 worsens with context length; Phase-5 transcript replay is the structural
    fix, so replay-on long-context runs should show a lower #21 rate.

Events joined (structlog JSON Lines):

* ``agent_loop.run_completed``        -> per-run terminal state + token totals
* ``session_runner.agent_run_done``   -> trace -> session / provider / model
* ``session_runner.history_context_built`` -> per-build ``transcript_replay`` flag
* ``session_runner.transcript_persisted``   -> Phase-5 persistence actually firing

The replay flag lives on a session-keyed build event (no trace_id), so we attach
it to a run by taking the most recent build for that session at or before the
run's completion timestamp.

Usage::

    python -m scripts.analyze_replay_validation
    python -m scripts.analyze_replay_validation --log data/log.debug.jsonl --json
    python -m scripts.analyze_replay_validation --since 2026-06-28
"""

from __future__ import annotations

import argparse
import bisect
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterator

DEFAULT_LOG = Path("data/log.debug.jsonl")

RUN_COMPLETED = "agent_loop.run_completed"
RUN_DONE = "session_runner.agent_run_done"
HISTORY_BUILT = "session_runner.history_context_built"
TRANSCRIPT_PERSISTED = "session_runner.transcript_persisted"

# Context-length buckets (input tokens actually sent to the provider).
TOKEN_BUCKETS = [
    (0, 8_000, "<8k"),
    (8_000, 20_000, "8-20k"),
    (20_000, 40_000, "20-40k"),
    (40_000, 80_000, "40-80k"),
    (80_000, 10_000_000, "80k+"),
]


def iter_records(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if '"event"' not in line:
                continue
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                yield rec


def _to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1", "yes"):
            return True
        if v in ("false", "0", "no"):
            return False
    return None


def _bucket(tokens: int) -> str:
    for lo, hi, label in TOKEN_BUCKETS:
        if lo <= tokens < hi:
            return label
    return "?"


def collect(path: Path) -> dict[str, Any]:
    runs: dict[str, dict[str, Any]] = {}
    meta: dict[str, dict[str, Any]] = {}
    hist_by_session: dict[str, list[tuple[str, bool | None, int]]] = defaultdict(list)
    persist_by_day: Counter = Counter()
    # Every history_context_built event with its timestamp + replay flag, so the
    # report can count replay firing within an arbitrary ``since`` window rather
    # than all-time (the per-run join only keeps the most recent build per run).
    replay_builds: list[tuple[str, bool | None]] = []

    for rec in iter_records(path):
        ev = rec.get("event")
        ts = rec.get("timestamp", "")
        if ev == RUN_COMPLETED:
            tid = rec.get("trace_id")
            if not tid:
                continue
            runs[tid] = {
                "ts": ts,
                "day": ts[:10],
                "reason": rec.get("reason") or "unknown",
                "terminal_state": rec.get("terminal_state") or "unknown",
                "step": int(rec.get("step") or 0),
                "tool_calls": int(rec.get("tool_call_count") or 0),
                "finish_reason": rec.get("finish_reason") or "",
                "input_tokens": int(rec.get("total_input_tokens") or 0),
                "output_tokens": int(rec.get("total_output_tokens") or 0),
                "reasoning_tokens": int(rec.get("total_reasoning_tokens") or 0),
            }
        elif ev == RUN_DONE:
            tid = rec.get("trace_id")
            if tid:
                meta[tid] = {
                    "session": rec.get("session_id"),
                    "provider": rec.get("provider_id"),
                    "model": rec.get("effective_model"),
                }
        elif ev == HISTORY_BUILT:
            sess = rec.get("session_id")
            if sess:
                flag = _to_bool(rec.get("transcript_replay"))
                hist_by_session[sess].append(
                    (ts, flag, int(rec.get("message_count") or 0))
                )
                replay_builds.append((ts, flag))
        elif ev == TRANSCRIPT_PERSISTED:
            persist_by_day[ts[:10]] += 1

    # Sort each session's build history once for binary search.
    for sess in hist_by_session:
        hist_by_session[sess].sort()

    def replay_for(session: str | None, ts: str) -> tuple[bool | None, int] | None:
        if not session:
            return None
        lst = hist_by_session.get(session)
        if not lst:
            return None
        keys = [x[0] for x in lst]
        i = bisect.bisect_right(keys, ts) - 1
        if i < 0:
            return None
        _, replay, msg_count = lst[i]
        return replay, msg_count

    enriched: list[dict[str, Any]] = []
    for tid, r in runs.items():
        m = meta.get(tid, {})
        rep = replay_for(m.get("session"), r["ts"])
        enriched.append(
            {
                "trace": tid,
                "day": r["day"],
                "ts": r["ts"],
                "reason": r["reason"],
                "terminal_state": r["terminal_state"],
                "step": r["step"],
                "tool_calls": r["tool_calls"],
                "finish": r["finish_reason"],
                "tokens": r["input_tokens"],
                "session": m.get("session"),
                "provider": m.get("provider"),
                "model": m.get("model"),
                "replay": rep[0] if rep else None,
                "hist_msg_count": rep[1] if rep else None,
            }
        )

    return {
        "runs": enriched,
        "persist_by_day": dict(persist_by_day),
        "replay_builds": replay_builds,
    }


def _replay_counts(builds: list[tuple[str, bool | None]], since: str) -> dict[str, int]:
    """Count history_context_built replay flags within a ``since`` window.

    Keys are the stringified flag (``"True"``/``"False"``/``"None"``). Windowing
    matters: replay firing is a Phase-5 rollout signal, so an all-time count
    under a "since ..." heading would conflate pre-rollout and post-rollout
    builds and mislead the validation result.
    """
    counts: dict[str, int] = {"True": 0, "False": 0, "None": 0}
    for ts, flag in builds:
        if ts < since:
            continue
        counts[str(flag)] += 1
    return counts


def _rate(n: int, d: int) -> float:
    return (n / d) if d else 0.0


def _pct(n: int, d: int) -> str:
    return f"{_rate(n, d) * 100:5.1f}%" if d else "   n/a"


def is_21(r: dict[str, Any]) -> bool:
    """#21 candidate pool: ended as completed with zero tool calls."""
    return r["reason"] == "no_tool_calls"


def report(data: dict[str, Any], since: str) -> str:
    runs = data["runs"]
    recent = [r for r in runs if r["ts"] >= since]
    lines: list[str] = []
    lines.append(
        f"Agent-loop repair validation  "
        f"({len(runs)} runs all-time, {len(recent)} since {since})"
    )
    lines.append("")

    # --- per-day #21 trend (shows the version transition) ---
    lines.append("Per-day #21 trend (reason=no_tool_calls / total runs):")
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in runs:
        by_day[r["day"]].append(r)
    lines.append(
        f"  {'day':12} {'runs':>5} {'no_tc':>6} {'rate':>7} {'replay?':>7}  notes"
    )
    for day in sorted(by_day):
        rs = by_day[day]
        ntc = sum(1 for r in rs if is_21(r))
        # replay coverage that day (any True joined)
        replay_true = sum(1 for r in rs if r["replay"] is True)
        replay_known = sum(1 for r in rs if r["replay"] is not None)
        notes = []
        if replay_known:
            notes.append(f"replay {replay_true}/{replay_known} on")
        lines.append(
            f"  {day:12} {len(rs):>5} {ntc:>6} {_pct(ntc, len(rs)):>7} "
            f"{(replay_true if replay_known else 0):>7}  {'; '.join(notes)}"
        )
    lines.append("")

    # --- replay firing (is Phase 5 actually live in recent window?) ---
    lines.append(f"Phase-5 replay firing (since {since}):")
    rfc = _replay_counts(data["replay_builds"], since)
    lines.append(
        f"  history_context_built transcript_replay  "
        f"True={rfc['True']}  False={rfc['False']}  "
        f"None/unknown={rfc['None']}"
    )
    recent_build_true = sum(1 for r in recent if r["replay"] is True)
    recent_build_false = sum(1 for r in recent if r["replay"] is False)
    recent_build_unknown = sum(1 for r in recent if r["replay"] is None)
    lines.append(
        f"  per-run replay join (recent)            "
        f"True={recent_build_true}  False={recent_build_false}  "
        f"unknown={recent_build_unknown}"
    )
    persist = data["persist_by_day"]
    recent_persist = sum(c for d, c in persist.items() if d >= since[:10])
    lines.append(
        f"  transcript_persisted events             "
        f"recent={recent_persist}  all-time={sum(persist.values())}"
    )
    lines.append("")

    # --- terminal outcome distribution (recent) ---
    lines.append(f"Terminal outcomes (since {since}, {len(recent)} runs):")
    sr = Counter(f"{r['terminal_state']}:{r['reason']}" for r in recent)
    for key, c in sr.most_common():
        lines.append(f"  {c:>4} {_pct(c, len(recent))}  {key}")
    lines.append("")

    # --- THE key cut: #21 vs context length, split by replay flag (recent) ---
    lines.append("#21 rate vs context length (input tokens), recent, by replay flag:")
    lines.append(f"  {'bucket':8} {'replay':>6} {'runs':>5} {'no_tc':>6} {'rate':>7}")
    for _, _, label in TOKEN_BUCKETS + [(0, 0, "?")]:
        for replay_val, tag in ((True, "on"), (False, "off")):
            rs = [
                r
                for r in recent
                if _bucket(r["tokens"]) == label and r["replay"] is replay_val
            ]
            if not rs:
                continue
            ntc = sum(1 for r in rs if is_21(r))
            lines.append(
                f"  {label:8} {tag:>6} {len(rs):>5} {ntc:>6} {_pct(ntc, len(rs)):>7}"
            )
    # and the replay-unknown row (older builds / un-joined)
    rs = [r for r in recent if r["replay"] is None]
    if rs:
        for _, _, label in TOKEN_BUCKETS:
            sub = [r for r in rs if _bucket(r["tokens"]) == label]
            if not sub:
                continue
            ntc = sum(1 for r in sub if is_21(r))
            lines.append(
                f"  {label:8} {'?':>6} {len(sub):>5} {ntc:>6} {_pct(ntc, len(sub)):>7}"
            )
    lines.append("")

    # --- #21 vs replay flag overall (recent) ---
    lines.append("#21 rate vs replay flag (recent, all lengths):")
    for replay_val, tag in ((True, "replay on"), (False, "replay off")):
        rs = [r for r in recent if r["replay"] is replay_val]
        if not rs:
            continue
        ntc = sum(1 for r in rs if is_21(r))
        med = sorted(r["tokens"] for r in rs)[len(rs) // 2]
        lines.append(
            f"  {tag:10} runs={len(rs):>4} no_tc={ntc:>4} "
            f"{_pct(ntc, len(rs)):>7}  median_tokens={med}"
        )
    lines.append("")

    # --- by model (recent) ---
    lines.append("#21 rate by model (recent):")
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in recent:
        by_model[r["model"] or "?"].append(r)
    for model, rs in sorted(by_model.items(), key=lambda kv: -len(kv[1])):
        ntc = sum(1 for r in rs if is_21(r))
        replay_on = sum(1 for r in rs if r["replay"] is True)
        lines.append(
            f"  {model:24} runs={len(rs):>4} no_tc={ntc:>4} "
            f"{_pct(ntc, len(rs)):>7}  replay_on={replay_on}"
        )
    lines.append("")

    # --- finish_reason + step distribution for no_tool_calls runs (recent) ---
    ntc_runs = [r for r in recent if is_21(r)]
    if ntc_runs:
        lines.append(f"#21 runs detail ({len(ntc_runs)} since {since}):")
        fr = Counter(r["finish"] or "(none)" for r in ntc_runs)
        lines.append(
            "  finish_reason: " + ", ".join(f"{k}={v}" for k, v in fr.most_common())
        )
        steps = Counter(r["step"] for r in ntc_runs)
        lines.append(
            "  step: " + ", ".join(f"step{k}={v}" for k, v in sorted(steps.items()))
        )
        big = [r for r in ntc_runs if r["tokens"] >= 40_000]
        lines.append(
            f"  #21 at >=40k tokens: {len(big)} "
            f"(of {sum(1 for r in recent if r['tokens'] >= 40000)} long runs)"
        )
    lines.append("")
    lines.append(
        "Caveat: no_tool_calls is the #21 CANDIDATE pool, not confirmed false-claims."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate agent-loop #21/#24 repair against real logs."
    )
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument(
        "--since",
        default="2026-06-28",
        help="ISO date/time threshold for the 'recent' window.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if not args.log.exists():
        print(f"log not found: {args.log}", flush=True)
        return 2

    data = collect(args.log)
    if args.json:
        print(
            json.dumps(
                {
                    "persist_by_day": data["persist_by_day"],
                    "replay_flag_counter_since": _replay_counts(
                        data["replay_builds"], args.since
                    ),
                    "recent_runs": [r for r in data["runs"] if r["ts"] >= args.since],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(report(data, args.since))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
