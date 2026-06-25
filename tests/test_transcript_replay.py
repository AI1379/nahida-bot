"""Tests for cross-turn tool transcript replay (agent-loop repair Phase 5).

Covers :mod:`nahida_bot.agent.runtime.transcript` (serialization, pairing
repair, the projector) and the migration 020 ``transcript_json`` column.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from nahida_bot.agent.context import ContextBuilder, ContextMessage
from nahida_bot.agent.providers import OpenAICompatibleProvider
from nahida_bot.agent.providers.anthropic import AnthropicProvider
from nahida_bot.agent.providers.base import ModelCapabilities
from nahida_bot.agent.runtime.models import AgentRunContext
from nahida_bot.agent.runtime.transcript import (
    TRANSCRIPT_TOOL_OUTPUT_MAX_CHARS,
    TranscriptProjector,
    message_from_dict,
    message_to_dict,
    repair_pairs,
    transcript_to_payload,
)
from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.db.repositories.sqlite_agent_run_repo import SQLiteAgentRunStore


# ── message builders mirroring how the loop constructs them ────────────


def _user(text: str) -> ContextMessage:
    return ContextMessage(role="user", source="user_input", content=text)


def _assistant(text: str, *calls: tuple[str, str, dict[str, Any]]) -> ContextMessage:
    metadata: dict[str, Any] | None = None
    if calls:
        metadata = {
            "tool_calls": [
                {"id": call_id, "name": name, "arguments": args}
                for name, call_id, args in calls
            ]
        }
    return ContextMessage(
        role="assistant", source="agent_response", content=text, metadata=metadata
    )


def _tool(
    name: str, call_id: str, output: str = "ok", status: str = "ok"
) -> ContextMessage:
    return ContextMessage(
        role="tool",
        source=f"tool_result:{name}",
        content=json.dumps(
            {"status": status, "output": output, "error": None, "logs": []},
            ensure_ascii=False,
            sort_keys=True,
        ),
        metadata={"tool_call_id": call_id, "tool_name": name},
    )


def _ctx(run_id: str, session_id: str = "sess_1") -> AgentRunContext:
    return AgentRunContext(
        run_id=run_id,
        trace_id=run_id,
        session_id=session_id,
        workspace_id=None,
        provider_id="prov_1",
    )


@pytest.fixture
async def engine():
    eng = DatabaseEngine(":memory:")
    await eng.initialize()
    try:
        yield eng
    finally:
        await eng.close()


@pytest.fixture
async def store(engine):
    return SQLiteAgentRunStore(engine)


async def _seed(
    store: SQLiteAgentRunStore,
    run_id: str,
    *,
    session_id: str = "sess_1",
    started_at: str,
    messages: list[ContextMessage],
) -> None:
    await store.create_run(
        _ctx(run_id, session_id), model="m", api_family="openai", started_at=started_at
    )
    await store.save_transcript(run_id, transcript_to_payload(messages))


# ── migration 020 ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_migration_adds_transcript_column_and_bumps_version() -> None:
    eng = DatabaseEngine(":memory:")
    await eng.initialize()
    try:
        cur = await eng.db.execute("SELECT version FROM schema_version")
        assert int((await cur.fetchone())["version"]) == 20

        cols = {
            str(r["name"]) for r in await eng.fetch_all("PRAGMA table_info(agent_runs)")
        }
        assert "transcript_json" in cols

        tables = {
            r["name"]
            for r in await eng.fetch_all(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        # The Phase 1 ledger tables are untouched by migration 020.
        assert {"agent_runs", "agent_run_events", "agent_execution_receipts"} <= tables
    finally:
        await eng.close()


@pytest.mark.asyncio
async def test_migration_idempotent_on_reinit() -> None:
    # A second initialize() on the same engine must not raise "duplicate column".
    eng = DatabaseEngine(":memory:")
    await eng.initialize()
    await eng.close()
    eng2 = DatabaseEngine(":memory:")
    await eng2.initialize()
    try:
        cols = {
            str(r["name"])
            for r in await eng2.fetch_all("PRAGMA table_info(agent_runs)")
        }
        assert "transcript_json" in cols
    finally:
        await eng2.close()


# ── round-trip + provider serialization ────────────────────────────────


@pytest.mark.asyncio
async def test_round_trip_preserves_paired_transcript(store) -> None:
    transcript = [
        _user("check the config"),
        _assistant("reading", ("workspace_read", "call_1", {"path": "config.yaml"})),
        _tool("workspace_read", "call_1", output="debug: true"),
        _assistant("all done", ("exec", "call_2", {"command": "pytest"})),
        _tool("exec", "call_2", output="3 passed"),
        _assistant("tests pass"),
    ]
    await _seed(store, "run_1", started_at="2026-06-25T00:00:00Z", messages=transcript)

    projector = TranscriptProjector(store)
    out = await projector.project(
        "sess_1", capabilities=ModelCapabilities(tool_calling=True)
    )

    roles = [m.role for m in out]
    # user + 2 (assistant+tool) pairs + final assistant
    assert roles == ["user", "assistant", "tool", "assistant", "tool", "assistant"]
    # call ids survive and pair correctly
    assert out[1].metadata["tool_calls"][0]["id"] == "call_1"
    assert out[2].metadata["tool_call_id"] == "call_1"
    assert out[3].metadata["tool_calls"][0]["id"] == "call_2"
    assert out[4].metadata["tool_call_id"] == "call_2"
    # source tagged for log/group recognition
    assert all(m.source.startswith("transcript_replay:run_1") for m in out)


@pytest.mark.asyncio
async def test_projected_output_serializes_for_openai_and_anthropic(store) -> None:
    transcript = [
        _user("run it"),
        _assistant("ok", ("exec", "call_1", {"command": "echo hi"})),
        _tool("exec", "call_1", output="hi"),
        _assistant("done"),
    ]
    await _seed(store, "run_1", started_at="2026-06-25T00:00:00Z", messages=transcript)

    projector = TranscriptProjector(store)
    out = await projector.project(
        "sess_1", capabilities=ModelCapabilities(tool_calling=True)
    )

    # OpenAI Chat Completions: assistant tool_calls + tool role with tool_call_id.
    openai_payload = OpenAICompatibleProvider(
        base_url="https://x", api_key="x", model="m"
    ).serialize_messages(out)
    asst = next(
        p
        for p in openai_payload
        if p.get("role") == "assistant" and p.get("tool_calls")
    )
    assert asst["tool_calls"][0]["function"]["name"] == "exec"
    tool_msg = next(p for p in openai_payload if p.get("role") == "tool")
    assert tool_msg["tool_call_id"] == "call_1"

    # Anthropic Messages: tool_use block on assistant, tool_result block present.
    _, anthropic_payload = AnthropicProvider(
        base_url="https://x", api_key="x", model="claude-test"
    )._serialize_messages_anthropic(out)
    block_types = {
        b.get("type")
        for p in anthropic_payload
        for b in (p.get("content") if isinstance(p.get("content"), list) else [])
    }
    assert "tool_use" in block_types
    assert "tool_result" in block_types


# ── pairing repair ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_orphan_tool_call_gets_synthetic_result(store) -> None:
    # Interrupted run: assistant emitted a tool call but no result followed.
    transcript = [
        _user("do thing"),
        _assistant("calling", ("exec", "call_1", {"command": "x"})),
    ]
    await _seed(store, "run_1", started_at="2026-06-25T00:00:00Z", messages=transcript)

    out = await TranscriptProjector(store).project(
        "sess_1", capabilities=ModelCapabilities(tool_calling=True)
    )
    roles = [m.role for m in out]
    assert roles == ["user", "assistant", "tool"]
    synthetic = out[2]
    assert synthetic.metadata["tool_call_id"] == "call_1"
    assert synthetic.metadata.get("synthetic") is True
    assert json.loads(synthetic.content)["status"] == "interrupted"


def test_repair_pairs_drops_orphan_tool_result() -> None:
    messages = [
        _tool("exec", "call_orphan", output="stray"),  # no preceding assistant call
        _assistant("hi"),
    ]
    repaired = repair_pairs(messages)
    # orphan result is dropped; the assistant text survives
    assert [m.role for m in repaired] == ["assistant"]
    assert repaired[0].content == "hi"


def test_repair_pairs_keeps_real_result_does_not_synthesize() -> None:
    messages = [
        _assistant("go", ("exec", "call_1", {})),
        _tool("exec", "call_1", output="done"),
    ]
    repaired = repair_pairs(messages)
    assert [m.role for m in repaired] == ["assistant", "tool"]
    assert json.loads(repaired[1].content)["output"] == "done"


# ── capability gating ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_tool_calling_collapses_to_text_summary(store) -> None:
    transcript = [
        _user("run it"),
        _assistant("ok", ("exec", "call_1", {"command": "x"})),
        _tool("exec", "call_1", output="hi"),
        _assistant("the result was hi"),
    ]
    await _seed(store, "run_1", started_at="2026-06-25T00:00:00Z", messages=transcript)

    out = await TranscriptProjector(store).project(
        "sess_1", capabilities=ModelCapabilities(tool_calling=False)
    )
    # A model that can't call tools gets one factual assistant summary per run.
    assert len(out) == 1
    assert out[0].role == "assistant"
    assert "Verified execution receipts this turn:" in out[0].content
    assert "call_1" in out[0].content
    assert "exec" in out[0].content
    # The run's natural-language answer is preserved as the prose prefix.
    assert "the result was hi" in out[0].content


# ── legacy fallback ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_transcripts_returns_empty(store) -> None:
    out = await TranscriptProjector(store).project(
        "sess_1", capabilities=ModelCapabilities(tool_calling=True)
    )
    assert out == []


@pytest.mark.asyncio
async def test_null_transcript_excluded(store) -> None:
    # A run row with no transcript (legacy / flag was off) must be excluded.
    await store.create_run(
        _ctx("run_legacy"),
        model="m",
        api_family="openai",
        started_at="2026-06-25T00:00:00Z",
    )
    runs = await store.list_recent_transcripts("sess_1")
    assert runs == []


@pytest.mark.asyncio
async def test_multiple_runs_ordered_oldest_first(store) -> None:
    await _seed(
        store,
        "run_new",
        started_at="2026-06-25T10:00:00Z",
        messages=[_user("late"), _assistant("late ans")],
    )
    await _seed(
        store,
        "run_old",
        started_at="2026-06-25T09:00:00Z",
        messages=[_user("early"), _assistant("early ans")],
    )
    out = await TranscriptProjector(store).project(
        "sess_1", capabilities=ModelCapabilities(tool_calling=True)
    )
    # oldest run first
    assert out[0].content == "early"
    assert out[-1].content == "late ans"


# ── atomic grouping (regression lock for budget trim) ──────────────────


@pytest.mark.asyncio
async def test_projected_transcript_forms_atomic_group(store) -> None:
    transcript = [
        _user("go"),
        _assistant("calling", ("exec", "call_1", {}), ("exec", "call_2", {})),
        _tool("exec", "call_1"),
        _tool("exec", "call_2"),
        _assistant("done"),
    ]
    await _seed(store, "run_1", started_at="2026-06-25T00:00:00Z", messages=transcript)
    out = await TranscriptProjector(store).project(
        "sess_1", capabilities=ModelCapabilities(tool_calling=True)
    )

    groups = ContextBuilder()._tool_transcript_groups(out)
    # The assistant(tool_calls) + its 2 tool results must be a single group so
    # _sliding_window_with_suffix cannot split them across a budget boundary.
    group_sizes = [len(g) for g in groups]
    assert 3 in group_sizes  # assistant + 2 tools grouped atomically


# ── output capping ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_output_capped_at_write_and_read(store) -> None:
    big = "x" * 10_000
    transcript = [
        _user("go"),
        _assistant("go", ("exec", "call_1", {})),
        _tool("exec", "call_1", output=big),
        _assistant("done"),
    ]
    await _seed(store, "run_1", started_at="2026-06-25T00:00:00Z", messages=transcript)

    # Stored payload is capped.
    rows = await store.list_recent_transcripts("sess_1")
    stored = json.loads(rows[0]["transcript_json"])
    tool_stored = next(m for m in stored if m["role"] == "tool")
    assert len(tool_stored["content"]) <= TRANSCRIPT_TOOL_OUTPUT_MAX_CHARS + 30

    # Projected output is also capped.
    out = await TranscriptProjector(store).project(
        "sess_1", capabilities=ModelCapabilities(tool_calling=True)
    )
    tool_msg = next(m for m in out if m.role == "tool")
    assert len(tool_msg.content) <= TRANSCRIPT_TOOL_OUTPUT_MAX_CHARS + 30


# ── serialization symmetry ─────────────────────────────────────────────


def test_message_to_dict_round_trip() -> None:
    msg = _assistant("hi", ("exec", "call_1", {"command": "echo"}))
    decoded = message_from_dict(message_to_dict(msg))
    assert decoded.role == msg.role
    assert decoded.content == msg.content
    assert decoded.metadata == msg.metadata
