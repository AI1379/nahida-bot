"""Tests for cross-turn tool transcript replay (agent-loop repair Phase 5).

Covers :mod:`nahida_bot.agent.runtime.transcript` (serialization, pairing
repair, the projector) and the migration 020 ``transcript_json`` column.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest

from nahida_bot.agent.context import ContextMessage, tool_transcript_groups
from nahida_bot.agent.context import truncate_messages_to_window
from nahida_bot.agent.providers import OpenAICompatibleProvider
from nahida_bot.agent.providers._tool_protocol import sanitize_tool_transcript
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
        assert int((await cur.fetchone())["version"]) == 21

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


# ── since filter (group dialogue-continuity gate, issue #37) ───────────


@pytest.mark.asyncio
async def test_project_since_drops_runs_started_before_cutoff(store) -> None:
    # Two runs in the same session: an old one and a recent one.
    await _seed(
        store,
        "run_old",
        started_at="2026-06-25T00:00:00Z",
        messages=[_user("old question"), _assistant("old answer")],
    )
    await _seed(
        store,
        "run_recent",
        started_at="2026-06-25T01:30:00Z",
        messages=[_user("recent question"), _assistant("recent answer")],
    )

    projector = TranscriptProjector(store)
    cutoff = datetime.fromisoformat("2026-06-25T01:00:00+00:00")

    filtered = await projector.project(
        "sess_1",
        capabilities=ModelCapabilities(tool_calling=False),
        since=cutoff,
    )
    contents = [m.content for m in filtered]
    assert "recent answer" in contents
    assert "old answer" not in contents

    # Without `since`, both runs are replayed (legacy behavior preserved).
    all_out = await projector.project(
        "sess_1", capabilities=ModelCapabilities(tool_calling=False)
    )
    assert "old answer" in [m.content for m in all_out]


@pytest.mark.asyncio
async def test_project_since_keeps_run_when_started_at_unparseable(store) -> None:
    # A run whose started_at cannot be parsed must be kept (fail-safe),
    # never silently dropped by the since filter.
    await _seed(
        store,
        "run_weird",
        started_at="2026-06-25T00:00:00Z",
        messages=[_user("keep me"), _assistant("ok")],
    )
    # Corrupt the started_at directly so _run_started_before hits the
    # unparseable branch.
    await store._engine.db.execute(
        "UPDATE agent_runs SET started_at = ? WHERE run_id = ?",
        ("not-a-date", "run_weird"),
    )
    await store._engine.db.commit()

    cutoff = datetime.fromisoformat("2030-01-01T00:00:00+00:00")
    out = await TranscriptProjector(store).project(
        "sess_1",
        capabilities=ModelCapabilities(tool_calling=False),
        since=cutoff,
    )
    assert "ok" in [m.content for m in out]


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

    groups = tool_transcript_groups(out)
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


# ── shared tool-transcript sanitization + truncation ────────────────────
#
# Regression coverage for the minimax ``provider_bad_response`` 400: an orphan
# tool_result (its tool_use dropped by a window cut) must never reach either
# provider family's wire format.


def test_sanitize_drops_orphan_tool_result() -> None:
    messages = [_tool("exec", "orphan"), _assistant("hi")]
    sanitized = sanitize_tool_transcript(messages, provider_name="test")
    assert [m.role for m in sanitized] == ["assistant"]


def test_sanitize_drops_incomplete_group() -> None:
    # Assistant declared 3 calls but only 2 results follow → whole group drops.
    messages = [
        _assistant(
            "go",
            ("exec", "c1", {}),
            ("exec", "c2", {}),
            ("exec", "c3", {}),
        ),
        _tool("exec", "c1"),
        _tool("exec", "c2"),
    ]
    sanitized = sanitize_tool_transcript(messages, provider_name="test")
    assert sanitized == []


def test_sanitize_keeps_complete_group() -> None:
    messages = [
        _assistant(
            "go",
            ("exec", "c1", {}),
            ("exec", "c2", {}),
            ("exec", "c3", {}),
        ),
        _tool("exec", "c1"),
        _tool("exec", "c2"),
        _tool("exec", "c3"),
    ]
    sanitized = sanitize_tool_transcript(messages, provider_name="test")
    assert [m.role for m in sanitized] == ["assistant", "tool", "tool", "tool"]
    # Order preserved: assistant then its results in declared order.
    assert sanitized[0].metadata["tool_calls"][0]["id"] == "c1"


def test_anthropic_serialize_never_emits_empty_tool_use_id() -> None:
    # An orphan tool_result fed straight to the Anthropic serializer must be
    # dropped, never emitted with an empty tool_use_id (that triggers 2013).
    messages = [
        _tool("exec", "orphan"),
        _assistant("hi"),
    ]
    _, payload = AnthropicProvider(
        base_url="https://x", api_key="x", model="claude-test"
    )._serialize_messages_anthropic(messages)
    tool_use_ids = {
        b.get("tool_use_id")
        for p in payload
        for b in (p.get("content") if isinstance(p.get("content"), list) else [])
        if isinstance(b, dict) and b.get("type") == "tool_result"
    }
    assert "" not in tool_use_ids
    assert tool_use_ids == set()  # the orphan was dropped entirely


@pytest.mark.asyncio
async def test_truncated_window_then_serialize_has_no_orphan(store) -> None:
    # Full-path regression: a multi-run history where a 6-parallel-call
    # assistant turn sits at the window boundary. After pair-aware truncation
    # + provider sanitization, neither provider sees an orphan tool id.
    multi_call_turn = [
        _assistant(
            "spawning",
            *(
                ("agent_spawn", f"call_function_tc44cz7oj9vn_{n}", {})
                for n in range(1, 7)
            ),
        )
    ] + [_tool("agent_spawn", f"call_function_tc44cz7oj9vn_{n}") for n in range(1, 7)]
    transcript = [_user("go"), *multi_call_turn, _assistant("done")]
    await _seed(
        store, "run_multi", started_at="2026-06-29T00:00:00Z", messages=transcript
    )

    replay = await TranscriptProjector(store).project(
        "sess_1", capabilities=ModelCapabilities(tool_calling=True)
    )
    # Pad with filler so the window cut lands inside the multi-call turn.
    padded = [_user(f"filler {i}") for i in range(210)] + replay
    window = truncate_messages_to_window(padded, 200)

    # Every tool result in the window is answerable by an assistant in it.
    declared: set[str] = set()
    for m in window:
        if m.role == "assistant" and isinstance(m.metadata, dict):
            raw = m.metadata.get("tool_calls")
            if isinstance(raw, list):
                for tc in raw:
                    if isinstance(tc, dict) and isinstance(tc.get("id"), str):
                        declared.add(tc["id"])
    for m in window:
        if m.role == "tool" and isinstance(m.metadata, dict):
            assert m.metadata.get("tool_call_id") in declared

    # And both provider wire formats stay orphan-free.
    openai_payload = OpenAICompatibleProvider(
        base_url="https://x", api_key="x", model="m"
    ).serialize_messages(window)
    openai_call_ids = {
        p.get("tool_call_id") for p in openai_payload if p.get("role") == "tool"
    }
    assert "" not in openai_call_ids

    _, anthropic_payload = AnthropicProvider(
        base_url="https://x", api_key="x", model="claude-test"
    )._serialize_messages_anthropic(window)
    anthropic_tool_use_ids = {
        b.get("tool_use_id")
        for p in anthropic_payload
        for b in (p.get("content") if isinstance(p.get("content"), list) else [])
        if isinstance(b, dict) and b.get("type") == "tool_result"
    }
    assert "" not in anthropic_tool_use_ids
