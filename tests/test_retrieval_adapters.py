from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from nahida_bot.agent.retrieval import (
    DocumentStoreRetrievalAdapter,
    MemoryStoreRetrievalAdapter,
    RetrievalRequest,
    RetrievalScope,
    RetrievalService,
)
from nahida_bot.agent.storage.models import SearchResult


class _DocStore:
    def __init__(self) -> None:
        self.hybrid_error: Exception | None = None
        self.hybrid_calls: list[tuple[str, int]] = []
        self.search_calls: list[tuple[str, int]] = []
        self.hybrid_results = [
            SearchResult(doc_id="doc_h", title="Hybrid", content="hybrid")
        ]
        self.search_results = [SearchResult(doc_id="doc_f", title="FTS", content="fts")]

    async def search_hybrid(
        self,
        query: str,
        provider: Any,
        *,
        limit: int = 10,
        vector_index: Any | None = None,
    ) -> list[SearchResult]:
        self.hybrid_calls.append((query, limit))
        if self.hybrid_error is not None:
            raise self.hybrid_error
        return self.hybrid_results[:limit]

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        self.search_calls.append((query, limit))
        return self.search_results[:limit]


class _MemoryStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, int]] = []

    async def search_items(
        self,
        query: str = "",
        *,
        scope_type: str = "global",
        scope_id: str = "__global__",
        limit: int = 10,
    ) -> list[Any]:
        self.calls.append((query, scope_type, scope_id, limit))
        if scope_type == "chat":
            return [
                SimpleNamespace(
                    item_id="mem_chat",
                    title="Chat",
                    content="Alice likes Python",
                    score=1.0,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    kind="preference",
                    source="consolidation",
                    sensitivity="private",
                    evidence={},
                    metadata={},
                )
            ]
        return [
            SimpleNamespace(
                item_id="mem_global",
                title="Global",
                content="Project uses Python",
                score=0.5,
                scope_type=scope_type,
                scope_id=scope_id,
                kind="decision",
                source="consolidation",
                sensitivity="private",
                evidence={},
                metadata={},
            )
        ][:limit]


@pytest.mark.asyncio
async def test_document_adapter_falls_back_to_fts_when_hybrid_fails() -> None:
    store = _DocStore()
    store.hybrid_error = RuntimeError("vector unavailable")
    adapter = DocumentStoreRetrievalAdapter(
        collection_name="docs",
        store=store,
        ensure_vector_ready=lambda: _ready_vector(),
    )

    results = await adapter.retrieve(
        RetrievalRequest(
            query="Python",
            source_type="knowledge_base",
            collection="docs",
            limit=2,
            fts_enabled=True,
            vector_enabled=True,
            hybrid_enabled=True,
        )
    )

    assert [result.result_id for result in results] == ["doc_f"]
    assert results[0].provenance is not None
    assert results[0].provenance.collection == "docs"
    assert store.hybrid_calls == [("Python", 2)]
    assert store.search_calls == [("Python", 2)]


@pytest.mark.asyncio
async def test_memory_adapter_cascades_chat_then_global() -> None:
    store = _MemoryStore()
    adapter = MemoryStoreRetrievalAdapter(memory_store=store)

    results = await adapter.retrieve(
        RetrievalRequest(
            query="Python",
            source_type="memory",
            scope=RetrievalScope("chat", "milky:private:10001"),
            limit=2,
            allow_global_fallback=True,
        )
    )

    assert [result.result_id for result in results] == ["mem_chat", "mem_global"]
    assert [result.metadata["scope_type"] for result in results] == ["chat", "global"]
    assert store.calls == [
        ("Python", "chat", "milky:private:10001", 2),
        ("Python", "global", "__global__", 1),
    ]


@pytest.mark.asyncio
async def test_retrieval_service_dispatches_registered_adapter() -> None:
    store = _MemoryStore()
    service = RetrievalService(
        {"memory": MemoryStoreRetrievalAdapter(memory_store=store)}
    )

    results = await service.retrieve(
        RetrievalRequest(query="Python", source_type="memory", limit=1)
    )

    assert [result.result_id for result in results] == ["mem_global"]


@pytest.mark.asyncio
async def test_memory_adapter_degrades_hybrid_to_fts() -> None:
    """A store lacking search_items_hybrid falls back to FTS rather than empty."""
    store = _MemoryStore()  # defines only search_items, no hybrid/vector variants
    adapter = MemoryStoreRetrievalAdapter(
        memory_store=store,
        embedding_provider=object(),  # keeps request.vector_enabled True
    )

    results = await adapter.retrieve(
        RetrievalRequest(
            query="Python",
            source_type="memory",
            limit=2,
            fts_enabled=True,
            vector_enabled=True,
            hybrid_enabled=True,  # selects hybrid mode despite the missing method
        )
    )

    # Hybrid mode is unavailable on this store, so the adapter degrades to FTS
    # and reports the mode it actually executed on every result.
    assert [result.result_id for result in results] == ["mem_global"]
    assert all(result.mode == "fts" for result in results)
    assert store.calls == [("Python", "global", "__global__", 2)]


async def _ready_vector() -> tuple[Any, None]:
    return object(), None


@pytest.mark.asyncio
async def test_memory_adapter_filters_results_below_min_score() -> None:
    store = _MemoryStore()
    adapter = MemoryStoreRetrievalAdapter(memory_store=store)

    # Default scope is global; the global fixture scores 0.5 and is dropped.
    results = await adapter.retrieve(
        RetrievalRequest(
            query="Python",
            source_type="memory",
            limit=2,
            min_score=0.7,
        )
    )

    assert results == []
    assert store.calls == [("Python", "global", "__global__", 2)]


@pytest.mark.asyncio
async def test_memory_adapter_threshold_keeps_strong_drops_weak_in_cascade() -> None:
    store = _MemoryStore()
    adapter = MemoryStoreRetrievalAdapter(memory_store=store)

    results = await adapter.retrieve(
        RetrievalRequest(
            query="Python",
            source_type="memory",
            scope=RetrievalScope("chat", "milky:private:10001"),
            limit=2,
            min_score=0.7,
            allow_global_fallback=True,
        )
    )

    # Chat hit scores 1.0 (kept); global fallback hit scores 0.5 (dropped).
    assert [result.result_id for result in results] == ["mem_chat"]
    assert [result.metadata["scope_type"] for result in results] == ["chat"]


@pytest.mark.asyncio
async def test_document_adapter_filters_results_below_min_score() -> None:
    store = _DocStore()
    store.search_results = [
        SearchResult(doc_id="doc_a", title="A", content="a", score=0.9),
        SearchResult(doc_id="doc_b", title="B", content="b", score=0.2),
    ]
    adapter = DocumentStoreRetrievalAdapter(collection_name="docs", store=store)

    results = await adapter.retrieve(
        RetrievalRequest(
            query="Python",
            source_type="knowledge_base",
            collection="docs",
            limit=5,
            fts_enabled=True,
            min_score=0.5,
        )
    )

    assert [result.result_id for result in results] == ["doc_a"]
