"""Canonical agent-run domain models (agent-loop repair Phase 1).

These types describe the *canonical* view of a single agent run: how it
terminated, what each tool execution produced (an :class:`ExecutionReceipt`),
and the context needed to persist it. They are storage-shaped; the agent loop
fills them via :class:`~nahida_bot.agent.runtime.recorder.RunRecorder`, and a
later phase (Phase 5) projects them back into provider messages for replay.

Phase 1 only *writes* these. No behaviour depends on them yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal


class TerminalState(StrEnum):
    """How an agent run ended.

    ``running`` is the initial persisted state; Phase 0-style classification
    (completed/incomplete/failed/cancelled) is recorded on finalization.
    ``unverified`` arrives in Phase 2/3 (claim-without-receipt) and is unused
    by the recorder in Phase 1.
    """

    RUNNING = "running"
    COMPLETED = "completed"
    UNVERIFIED = "unverified"
    INCOMPLETE = "incomplete"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Canonical event types stored in ``agent_run_events.event_type``.
EventType = Literal[
    "user_input",
    "assistant_output",
    "tool_call",
    "tool_result",
    "provider_anomaly",
    "terminal",
]

# Receipt status stored in ``agent_execution_receipts.status``.
ReceiptStatus = Literal["ok", "error", "cancelled", "timed_out"]

# Phase 1 never verifies; Phase 3 will compute verified/partial/unverified.
RECEIPT_VERIFICATION_UNVERIFIED = "unverified"


@dataclass(slots=True, frozen=True)
class AgentRunContext:
    """Per-run identifying context attached to every canonical record."""

    run_id: str
    trace_id: str = ""
    session_id: str | None = None
    workspace_id: str | None = None
    provider_id: str | None = None


@dataclass(slots=True, frozen=True)
class ExecutionReceipt:
    """Canonical, sanitized record of one tool execution.

    ``evidence`` holds derived signals (output summary + hash, error fields,
    attempt/phase). Strong evidence (message IDs, artifact IDs) is added per
    tool in later phases; Phase 1 derives a generic receipt for every tool.
    """

    receipt_id: str
    run_id: str
    call_id: str
    tool_name: str
    status: ReceiptStatus
    verification_status: str = RECEIPT_VERIFICATION_UNVERIFIED
    input_fingerprint: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    started_at: str = ""
    finished_at: str | None = None


@dataclass(slots=True, frozen=True)
class RunEvent:
    """One canonical event in a run's append-only stream."""

    event_id: int | None
    run_id: str
    sequence: int
    event_type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""


def utc_now_iso() -> str:
    """Current UTC timestamp in ISO-8601 (matches the repo's log/DB convention)."""
    return datetime.now(UTC).isoformat()
