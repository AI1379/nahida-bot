"""SQLite repository for the canonical agent-run ledger (Phase 1).

Persists :mod:`nahida_bot.agent.runtime` domain objects to the three Phase-1
tables (``agent_runs`` / ``agent_run_events`` / ``agent_execution_receipts``,
migration 019). Mirrors the write/read style of the other SQLite repositories
(``write_lock`` + ``execute`` + ``db.commit()``; ``fetch_one`` / ``fetch_all``
returning dicts).

Invariants enforced here:
- :meth:`append_event` rejects appends to a run that is no longer ``running``
  (raises :class:`AgentRunClosedError`).
- :meth:`finalize_run` is idempotent (only updates rows still ``running``).
- Duplicate ``(run_id, sequence)`` events are rejected by the schema's UNIQUE
  constraint.
"""

from __future__ import annotations

import json
from typing import Any

from nahida_bot.agent.runtime.models import (
    AgentRunContext,
    ExecutionReceipt,
    TerminalState,
)
from nahida_bot.agent.runtime.store import AgentRunClosedError, AgentRunStore
from nahida_bot.db.engine import DatabaseEngine


class SQLiteAgentRunStore(AgentRunStore):
    """SQLite-backed canonical run ledger."""

    def __init__(self, engine: DatabaseEngine) -> None:
        self._engine = engine

    async def create_run(
        self,
        context: AgentRunContext,
        *,
        model: str = "",
        api_family: str = "",
        started_at: str,
    ) -> None:
        async with self._engine.write_lock:
            await self._engine.execute(
                "INSERT INTO agent_runs "
                "(run_id, session_id, workspace_id, provider_id, model, "
                "api_family, terminal_state, completion_contract_json, "
                "trace_id, started_at, ended_at, failure_code, failure_detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL, NULL, NULL)",
                (
                    context.run_id,
                    context.session_id,
                    context.workspace_id,
                    context.provider_id,
                    model,
                    api_family,
                    TerminalState.RUNNING.value,
                    context.trace_id,
                    started_at,
                ),
            )
            await self._engine.db.commit()

    async def append_event(
        self,
        run_id: str,
        *,
        sequence: int,
        event_type: str,
        payload: dict[str, Any],
        created_at: str,
    ) -> None:
        async with self._engine.write_lock:
            row = await self._engine.fetch_one(
                "SELECT terminal_state FROM agent_runs WHERE run_id = ?",
                (run_id,),
            )
            if row is None:
                # No run row (e.g. create_run failed earlier) — nothing to
                # attach to. Best-effort: drop silently rather than wedge.
                return
            if str(row["terminal_state"]) != TerminalState.RUNNING.value:
                raise AgentRunClosedError(
                    f"run {run_id} is terminal ({row['terminal_state']}); "
                    f"cannot append {event_type} event"
                )
            await self._engine.execute(
                "INSERT INTO agent_run_events "
                "(run_id, sequence, event_type, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    run_id,
                    sequence,
                    event_type,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    created_at,
                ),
            )
            await self._engine.db.commit()

    async def record_receipt(self, receipt: ExecutionReceipt) -> None:
        evidence_json = json.dumps(receipt.evidence, ensure_ascii=False, sort_keys=True)
        async with self._engine.write_lock:
            await self._engine.execute(
                "INSERT INTO agent_execution_receipts "
                "(receipt_id, run_id, call_id, tool_name, status, "
                "verification_status, input_fingerprint, evidence_json, "
                "started_at, finished_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(run_id, call_id) DO UPDATE SET "
                "tool_name = excluded.tool_name, "
                "status = excluded.status, "
                "verification_status = excluded.verification_status, "
                "input_fingerprint = excluded.input_fingerprint, "
                "evidence_json = excluded.evidence_json, "
                "started_at = excluded.started_at, "
                "finished_at = excluded.finished_at",
                (
                    receipt.receipt_id,
                    receipt.run_id,
                    receipt.call_id,
                    receipt.tool_name,
                    receipt.status,
                    receipt.verification_status,
                    receipt.input_fingerprint,
                    evidence_json,
                    receipt.started_at,
                    receipt.finished_at,
                ),
            )
            await self._engine.db.commit()

    async def finalize_run(
        self,
        run_id: str,
        *,
        terminal_state: TerminalState,
        ended_at: str,
        failure_code: str = "",
        failure_detail: str = "",
    ) -> None:
        async with self._engine.write_lock:
            await self._engine.execute(
                "UPDATE agent_runs "
                "SET terminal_state = ?, ended_at = ?, "
                "failure_code = ?, failure_detail = ? "
                "WHERE run_id = ? AND terminal_state = ?",
                (
                    terminal_state.value,
                    ended_at,
                    failure_code or None,
                    failure_detail or None,
                    run_id,
                    TerminalState.RUNNING.value,
                ),
            )
            await self._engine.db.commit()

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = await self._engine.fetch_one(
            "SELECT run_id, session_id, workspace_id, provider_id, model, "
            "api_family, terminal_state, trace_id, started_at, ended_at, "
            "failure_code, failure_detail "
            "FROM agent_runs WHERE run_id = ?",
            (run_id,),
        )
        return dict(row) if row is not None else None

    async def list_events(self, run_id: str) -> list[dict[str, Any]]:
        rows = await self._engine.fetch_all(
            "SELECT event_id, run_id, sequence, event_type, payload_json, "
            "created_at FROM agent_run_events "
            "WHERE run_id = ? ORDER BY sequence",
            (run_id,),
        )
        return [dict(row) for row in rows]

    async def list_receipts(self, run_id: str) -> list[dict[str, Any]]:
        rows = await self._engine.fetch_all(
            "SELECT receipt_id, run_id, call_id, tool_name, status, "
            "verification_status, input_fingerprint, evidence_json, "
            "started_at, finished_at FROM agent_execution_receipts "
            "WHERE run_id = ? ORDER BY started_at",
            (run_id,),
        )
        return [dict(row) for row in rows]


__all__ = ["SQLiteAgentRunStore"]
