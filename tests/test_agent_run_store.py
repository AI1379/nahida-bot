"""Tests for the canonical agent-run ledger store (Phase 1)."""

from __future__ import annotations

import pytest

from nahida_bot.agent.runtime.models import AgentRunContext, TerminalState
from nahida_bot.agent.runtime.store import AgentRunClosedError
from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.db.repositories.sqlite_agent_run_repo import SQLiteAgentRunStore


def _ctx(run_id: str = "run_1") -> AgentRunContext:
    return AgentRunContext(
        run_id=run_id,
        trace_id=run_id,
        session_id="sess_1",
        workspace_id="ws_1",
        provider_id="prov_1",
    )


@pytest.fixture
async def store() -> SQLiteAgentRunStore:
    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    try:
        yield SQLiteAgentRunStore(engine)
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_migration_creates_ledger_tables_and_version() -> None:
    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    try:
        cur = await engine.db.execute("SELECT version FROM schema_version")
        row = await cur.fetchone()
        assert int(row["version"]) == 21
        names = {
            r["name"]
            for r in await engine.fetch_all(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"agent_runs", "agent_run_events", "agent_execution_receipts"} <= names
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_create_run_and_append_events_in_sequence(
    store: SQLiteAgentRunStore,
) -> None:
    await store.create_run(_ctx(), model="m", api_family="openai", started_at="t0")
    for seq, etype in enumerate(
        ("user_input", "assistant_output", "terminal"), start=1
    ):
        await store.append_event(
            "run_1",
            sequence=seq,
            event_type=etype,
            payload={"seq": seq},
            created_at=f"t{seq}",
        )

    run = await store.get_run("run_1")
    assert run is not None
    assert run["terminal_state"] == TerminalState.RUNNING.value
    assert run["model"] == "m"

    events = await store.list_events("run_1")
    assert [e["sequence"] for e in events] == [1, 2, 3]
    assert [e["event_type"] for e in events] == [
        "user_input",
        "assistant_output",
        "terminal",
    ]


@pytest.mark.asyncio
async def test_append_event_rejected_after_terminal(store: SQLiteAgentRunStore) -> None:
    await store.create_run(_ctx(), started_at="t0")
    await store.finalize_run(
        "run_1", terminal_state=TerminalState.COMPLETED, ended_at="t1"
    )
    with pytest.raises(AgentRunClosedError):
        await store.append_event(
            "run_1", sequence=1, event_type="user_input", payload={}, created_at="t2"
        )


@pytest.mark.asyncio
async def test_finalize_run_is_idempotent(store: SQLiteAgentRunStore) -> None:
    await store.create_run(_ctx(), started_at="t0")
    await store.finalize_run(
        "run_1", terminal_state=TerminalState.COMPLETED, ended_at="t1"
    )
    # Second finalize must not flip a finalized run (e.g. back to failed).
    await store.finalize_run(
        "run_1", terminal_state=TerminalState.FAILED, ended_at="t2"
    )
    run = await store.get_run("run_1")
    assert run["terminal_state"] == TerminalState.COMPLETED.value
    assert run["ended_at"] == "t1"


@pytest.mark.asyncio
async def test_record_receipt_upsert(store: SQLiteAgentRunStore) -> None:
    from nahida_bot.agent.runtime.models import ExecutionReceipt

    await store.create_run(_ctx(), started_at="t0")
    receipt = ExecutionReceipt(
        receipt_id="r1",
        run_id="run_1",
        call_id="c1",
        tool_name="read",
        status="ok",
        input_fingerprint="fp",
        evidence={"output_hash": "h1"},
        started_at="t0",
        finished_at="t1",
    )
    await store.record_receipt(receipt)
    # Upsert same (run_id, call_id) with new status/evidence.
    receipt2 = ExecutionReceipt(
        receipt_id="r2",
        run_id="run_1",
        call_id="c1",
        tool_name="read",
        status="error",
        input_fingerprint="fp",
        evidence={"output_hash": "h2"},
        started_at="t0",
        finished_at="t1",
    )
    await store.record_receipt(receipt2)

    receipts = await store.list_receipts("run_1")
    assert len(receipts) == 1
    assert receipts[0]["status"] == "error"
    assert receipts[0]["evidence_json"].__contains__("h2")


@pytest.mark.asyncio
async def test_duplicate_sequence_rejected(store: SQLiteAgentRunStore) -> None:
    await store.create_run(_ctx(), started_at="t0")
    await store.append_event(
        "run_1", sequence=1, event_type="user_input", payload={}, created_at="t1"
    )
    with pytest.raises(Exception):  # sqlite UNIQUE constraint → IntegrityError
        await store.append_event(
            "run_1",
            sequence=1,
            event_type="assistant_output",
            payload={},
            created_at="t2",
        )
