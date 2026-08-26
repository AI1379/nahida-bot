"""A1 cross-chat recall: the ``conversation_turns`` third retrieval source,
N-way weighted RRF fusion in RetrievalService, and the ``recall_cross_chat``
intent-triggered exploratory recall tool (memory-architecture-exploration §8
Step 2)."""

from __future__ import annotations

from typing import Any

import pytest

from nahida_bot.agent.memory.models import ConversationTurn, MemoryRecord
from nahida_bot.agent.memory.sqlite import SQLiteMemoryStore
from nahida_bot.agent.retrieval.adapters import ConversationTurnsRetrievalAdapter
from nahida_bot.agent.retrieval.models import (
    RetrievalRequest,
    RetrievalResult,
    RetrievalScope,
)
from nahida_bot.agent.retrieval.service import RetrievalService
from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.plugins.builtin.tools.history import HistoryTools


def _record(
    turn_id: int,
    session_id: str,
    role: str,
    content: str,
) -> MemoryRecord:
    return MemoryRecord(
        turn_id=turn_id,
        session_id=session_id,
        turn=ConversationTurn(role=role, content=content, source="milky"),
    )


class _TurnStore:
    """Fake structured store exposing only ``search_turns``."""

    def __init__(self, records: list[MemoryRecord]) -> None:
        self.records = records
        self.calls: list[dict[str, Any]] = []

    async def search_turns(
        self,
        query: str = "",
        *,
        chat_address: str = "",
        source: str = "",
        role: str = "",
        limit: int = 100,
    ) -> list[MemoryRecord]:
        self.calls.append(
            {
                "query": query,
                "chat_address": chat_address,
                "limit": limit,
            }
        )
        return self.records[:limit]


class _FixedAdapter:
    """Adapter stub returning one canned ranked list."""

    def __init__(
        self, results: list[RetrievalResult], *, error: Exception | None = None
    ) -> None:
        self.results = results
        self.error = error

    async def retrieve(self, request: RetrievalRequest) -> list[RetrievalResult]:
        if self.error is not None:
            raise self.error
        return self.results[: request.limit]


def _result(source_type: str, result_id: str) -> RetrievalResult:
    return RetrievalResult(
        result_id=result_id,
        title=result_id,
        text=f"text {result_id}",
        source_type=source_type,  # type: ignore[arg-type]
    )


# ── ConversationTurnsRetrievalAdapter ─────────────────────────────


@pytest.mark.asyncio
async def test_turns_adapter_converts_records() -> None:
    store = _TurnStore([_record(7, "milky:group:20001:topic", "user", "hello dragons")])
    adapter = ConversationTurnsRetrievalAdapter(memory_store=store)
    results = await adapter.retrieve(
        RetrievalRequest(query="dragons", source_type="conversation_turns", limit=5)
    )
    assert len(results) == 1
    result = results[0]
    assert result.source_type == "conversation_turns"
    assert result.result_id == "turn-7"
    assert result.mode == "fts"
    assert result.text == "hello dragons"
    assert result.provenance is not None
    assert result.provenance.scope_type == "chat"
    # derived session id collapses to the 3-part chat key
    assert result.provenance.scope_id == "milky:group:20001"
    assert result.metadata["chat_key"] == "milky:group:20001"
    assert result.metadata["session_id"] == "milky:group:20001:topic"
    assert result.metadata["role"] == "user"
    assert result.metadata["raw_turn"] is True


@pytest.mark.asyncio
async def test_turns_adapter_requires_query() -> None:
    store = _TurnStore([_record(1, "milky:group:1", "user", "x")])
    adapter = ConversationTurnsRetrievalAdapter(memory_store=store)
    assert (
        await adapter.retrieve(
            RetrievalRequest(query="   ", source_type="conversation_turns", limit=5)
        )
        == []
    )
    assert store.calls == []


@pytest.mark.asyncio
async def test_turns_adapter_passes_chat_scope_filter() -> None:
    store = _TurnStore([_record(1, "milky:group:1", "user", "x")])
    adapter = ConversationTurnsRetrievalAdapter(memory_store=store)
    await adapter.retrieve(
        RetrievalRequest(
            query="x",
            source_type="conversation_turns",
            limit=5,
            scopes=(RetrievalScope(scope_type="chat", scope_id="milky:group:1"),),
        )
    )
    assert store.calls[0]["chat_address"] == "milky:group:1"


# ── RetrievalService.retrieve_fused ───────────────────────────────


@pytest.mark.asyncio
async def test_fused_merges_sources_with_fused_score() -> None:
    service = RetrievalService(
        {
            "memory": _FixedAdapter([_result("memory", "m1"), _result("memory", "m2")]),
            "conversation_turns": _FixedAdapter([_result("conversation_turns", "t1")]),
        }
    )
    results = await service.retrieve_fused(
        [
            ("memory", RetrievalRequest(query="q", source_type="memory", limit=2)),
            (
                "conversation_turns",
                RetrievalRequest(query="q", source_type="conversation_turns", limit=2),
            ),
        ],
        limit=3,
    )
    assert {r.result_id for r in results} == {"m1", "m2", "t1"}
    for result in results:
        assert result.metadata["fused_score"] == result.score


@pytest.mark.asyncio
async def test_fused_mapping_weights_reorder() -> None:
    service = RetrievalService(
        {
            "memory": _FixedAdapter([_result("memory", "m1")]),
            "conversation_turns": _FixedAdapter([_result("conversation_turns", "t1")]),
        }
    )
    requests = [
        ("memory", RetrievalRequest(query="q", source_type="memory", limit=1)),
        (
            "conversation_turns",
            RetrievalRequest(query="q", source_type="conversation_turns", limit=1),
        ),
    ]
    # Equal weights: the earlier list wins the rank-1 tie (stable sort).
    equal = await service.retrieve_fused(
        requests, limit=2, weights={"memory": 1.0, "conversation_turns": 1.0}
    )
    assert equal[0].result_id == "m1"
    # Heavier turns weight outranks the tie.
    tilted = await service.retrieve_fused(
        requests, limit=2, weights={"memory": 0.1, "conversation_turns": 1.0}
    )
    assert tilted[0].result_id == "t1"


@pytest.mark.asyncio
async def test_fused_sequence_weights_survive_skipped_source() -> None:
    service = RetrievalService(
        {
            "memory": _FixedAdapter([_result("memory", "m1")]),
            "conversation_turns": _FixedAdapter([_result("conversation_turns", "t1")]),
            # no knowledge_base adapter registered → that request is skipped
        }
    )
    results = await service.retrieve_fused(
        [
            ("memory", RetrievalRequest(query="q", source_type="memory", limit=1)),
            (
                "knowledge_base",
                RetrievalRequest(query="q", source_type="knowledge_base", limit=1),
            ),
            (
                "conversation_turns",
                RetrievalRequest(query="q", source_type="conversation_turns", limit=1),
            ),
        ],
        limit=2,
        weights=[1.0, 5.0, 1.0],
    )
    # The skipped knowledge_base weight must not shift onto the turns leg:
    # memory and turns tie at rank 1 with weight 1.0 → memory (earlier list) wins.
    assert [r.result_id for r in results] == ["m1", "t1"]


@pytest.mark.asyncio
async def test_fused_skips_failing_source() -> None:
    service = RetrievalService(
        {
            "memory": _FixedAdapter([], error=RuntimeError("store down")),
            "conversation_turns": _FixedAdapter([_result("conversation_turns", "t1")]),
        }
    )
    results = await service.retrieve_fused(
        [
            ("memory", RetrievalRequest(query="q", source_type="memory", limit=1)),
            (
                "conversation_turns",
                RetrievalRequest(query="q", source_type="conversation_turns", limit=1),
            ),
        ],
        limit=2,
    )
    assert [r.result_id for r in results] == ["t1"]


@pytest.mark.asyncio
async def test_fused_sequence_weights_length_validated() -> None:
    service = RetrievalService({"memory": _FixedAdapter([_result("memory", "m1")])})
    with pytest.raises(ValueError, match="weights length"):
        await service.retrieve_fused(
            [("memory", RetrievalRequest(query="q", source_type="memory", limit=1))],
            limit=1,
            weights=[1.0, 2.0],
        )


# ── recall_cross_chat end-to-end over a real store ────────────────


@pytest.fixture
async def engine() -> DatabaseEngine:
    eng = DatabaseEngine(":memory:")
    await eng.initialize()
    yield eng
    await eng.close()


async def _seed_recall_store(engine: DatabaseEngine) -> SQLiteMemoryStore:
    """Two chats: A (current context) and B (where 'dragons' was discussed)."""
    store = SQLiteMemoryStore(engine)
    for session_id in ("milky:group:A", "milky:group:B"):
        await store.ensure_session(session_id)
    await store.append_turn(
        "milky:group:B",
        ConversationTurn(
            role="user", content="we adopted dragons as the mascot in this group"
        ),
    )
    await store.append_turn(
        "milky:group:A",
        ConversationTurn(role="user", content="dragons showed up here too"),
    )
    await store.append_item(
        title="mascot",
        content="The shared mascots are dragons",
        scope_type="global",
        scope_id="__global__",
        kind="fact",
        source="plugin",
        sensitivity="public",
    )
    await store.append_item(
        title="secret",
        content="Alice secretly fears dragons",
        scope_type="chat",
        scope_id="milky:group:B",
        kind="fact",
        source="plugin",
        sensitivity="private",
    )
    await store.append_item(
        title="not portable",
        content="Dragon trivia that must stay in chat B",
        scope_type="chat",
        scope_id="milky:group:B",
        kind="fact",
        source="plugin",
        sensitivity="public",
        metadata={"portable": False},
    )
    return store


@pytest.mark.asyncio
async def test_recall_cross_chat_hits_other_chat_turn(tmp_path) -> None:
    from tests.test_api_bridge import _api

    eng = DatabaseEngine(":memory:")
    await eng.initialize()
    try:
        store = await _seed_recall_store(eng)
        api, _, _, _ = _api(tmp_path, memory_store=store)
        rows = await api.recall_cross_chat("dragons", limit=8)
        assert rows, "expected fused recall hits"
        turn_hits = [r for r in rows if r["source_type"] == "conversation_turns"]
        assert {r["session_id"] for r in turn_hits} >= {"milky:group:B"}
        memory_hits = [r for r in rows if r["source_type"] == "memory"]
        assert any(r["result_id"] and "mascot" in r["title"] for r in memory_hits)
        # Fail closed: private and non-portable items never cross scopes.
        assert all("fears dragons" not in r["content"] for r in rows)
        assert all("must stay in chat B" not in r["content"] for r in rows)
    finally:
        await eng.close()


@pytest.mark.asyncio
async def test_recall_cross_chat_disabled(tmp_path) -> None:
    from tests.test_api_bridge import _api

    eng = DatabaseEngine(":memory:")
    await eng.initialize()
    try:
        store = await _seed_recall_store(eng)
        api, _, _, _ = _api(
            tmp_path,
            memory_store=store,
            memory_cross_chat_enabled=False,
        )
        assert await api.recall_cross_chat("dragons") == []
    finally:
        await eng.close()


@pytest.mark.asyncio
async def test_recall_cross_chat_chat_address_narrows_turns(tmp_path) -> None:
    from tests.test_api_bridge import _api

    eng = DatabaseEngine(":memory:")
    await eng.initialize()
    try:
        store = await _seed_recall_store(eng)
        api, _, _, _ = _api(tmp_path, memory_store=store)
        rows = await api.recall_cross_chat(
            "dragons", chat_address="milky:group:B", limit=8
        )
        turn_hits = [r for r in rows if r["source_type"] == "conversation_turns"]
        assert turn_hits
        assert {r["session_id"] for r in turn_hits} == {"milky:group:B"}
    finally:
        await eng.close()


# ── Tool rendering ────────────────────────────────────────────────


class _RecallAPI:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.names: dict[str, str] = {"milky:group:B": "原神交流群"}

    async def recall_cross_chat(
        self,
        query: str,
        *,
        chat_address: str = "",
        limit: int = 8,
        allowed_chats: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        return self.rows

    async def get_chat_names(self, chat_keys: list[str]) -> dict[str, str]:
        return {k: v for k, v in self.names.items() if k in chat_keys}


@pytest.mark.asyncio
async def test_history_tool_renders_provenance() -> None:
    api = _RecallAPI(
        [
            {
                "source_type": "conversation_turns",
                "result_id": "turn-3",
                "title": "[user] milky:group:B",
                "content": "we adopted a dragon mascot",
                "score": 0.03,
                "mode": "fts",
                "scope_type": "chat",
                "scope_id": "milky:group:B",
                "session_id": "milky:group:B",
                "chat_key": "milky:group:B",
                "role": "user",
                "created_at": "2026-08-20T00:00:00+00:00",
                "kind": "",
                "sensitivity": "",
                "turn_source": "milky",
            },
            {
                "source_type": "memory",
                "result_id": "mem_1",
                "title": "mascot",
                "content": "The shared mascot is a dragon",
                "score": 0.02,
                "mode": "fts",
                "scope_type": "global",
                "scope_id": "__global__",
                "session_id": "",
                "chat_key": "",
                "role": "",
                "created_at": "",
                "kind": "fact",
                "sensitivity": "public",
                "turn_source": "",
            },
        ]
    )
    tools = HistoryTools(api)  # type: ignore[arg-type]
    output = await tools.recall("dragons")
    assert "Soft recall" in output
    assert "do not surface another chat's private content" in output
    assert "[past turn] [原神交流群]" in output
    assert "[user]" in output
    assert "[memory] [global:__global__] fact mascot" in output


@pytest.mark.asyncio
async def test_history_tool_no_matches_hint() -> None:
    tools = HistoryTools(_RecallAPI([]))  # type: ignore[arg-type]
    output = await tools.recall("nothing-matches-this")
    assert "No cross-chat recall matched" in output


# ── Chat-domain narrowed turns leg (multi-scope OR) ────────────────


class _FilteringTurnStore:
    """Fake store honoring the ``chat_address`` filter."""

    def __init__(self, records: list[MemoryRecord]) -> None:
        self.records = records
        self.calls: list[str] = []

    async def search_turns(
        self,
        query: str = "",
        *,
        chat_address: str = "",
        source: str = "",
        role: str = "",
        limit: int = 100,
    ) -> list[MemoryRecord]:
        self.calls.append(chat_address)
        if not chat_address:
            return self.records[:limit]
        return [
            record
            for record in self.records
            if record.session_id.startswith(chat_address)
        ][:limit]


@pytest.mark.asyncio
async def test_turns_adapter_multiple_chat_scopes_search_each_and_merge() -> None:
    store = _FilteringTurnStore(
        [
            _record(1, "milky:group:100", "user", "dragons main"),
            _record(2, "milky:group:200", "user", "dragons sibling"),
            _record(3, "milky:group:300", "user", "dragons elsewhere"),
        ]
    )
    adapter = ConversationTurnsRetrievalAdapter(memory_store=store)
    results = await adapter.retrieve(
        RetrievalRequest(
            query="dragons",
            source_type="conversation_turns",
            limit=10,
            scopes=(
                RetrievalScope(scope_type="chat", scope_id="milky:group:100"),
                RetrievalScope(scope_type="chat", scope_id="milky:group:200"),
            ),
        )
    )
    assert sorted(store.calls) == ["milky:group:100", "milky:group:200"]
    texts = {result.text for result in results}
    assert texts == {"dragons main", "dragons sibling"}


@pytest.mark.asyncio
async def test_turns_adapter_interleaves_multi_scope_results_up_to_limit() -> None:
    records = [
        _record(i, "milky:group:100" if i % 2 else "milky:group:200", "user", f"hit{i}")
        for i in range(1, 9)
    ]
    store = _FilteringTurnStore(records)
    adapter = ConversationTurnsRetrievalAdapter(memory_store=store)
    results = await adapter.retrieve(
        RetrievalRequest(
            query="hit",
            source_type="conversation_turns",
            limit=3,
            scopes=(
                RetrievalScope(scope_type="chat", scope_id="milky:group:100"),
                RetrievalScope(scope_type="chat", scope_id="milky:group:200"),
            ),
        )
    )
    assert len(results) == 3


@pytest.mark.asyncio
async def test_turns_adapter_dedupes_duplicate_scopes() -> None:
    store = _FilteringTurnStore([_record(1, "milky:group:100", "user", "hit")])
    adapter = ConversationTurnsRetrievalAdapter(memory_store=store)
    await adapter.retrieve(
        RetrievalRequest(
            query="hit",
            source_type="conversation_turns",
            limit=5,
            scopes=(
                RetrievalScope(scope_type="chat", scope_id="milky:group:100"),
                RetrievalScope(scope_type="chat", scope_id="milky:group:100"),
            ),
        )
    )
    assert store.calls == ["milky:group:100"]
