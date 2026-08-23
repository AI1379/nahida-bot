"""Issue #49 retrieval fixes.

Covers the four production-verified defects and their regressions:

- weighted RRF fusion (vector rank-1 must outrank FTS keyword junk),
- amplified hybrid candidate pool (correct chunk outside FTS top-k still
  surfaces via fusion),
- positive, larger-is-better FTS scores with structural (title-only) nodes
  excluded from ranking,
- vector-index self-heal: a wiped index is rebuilt from persisted embeddings
  without any embedding API call,
- kb auto-recall delegation to the KB plugin (hybrid path) with the FTS-only
  direct-adapter fallback preserved.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nahida_bot.agent.storage.embedding import (
    EmbeddingResult,
    HashEmbeddingProvider,
)
from nahida_bot.agent.storage.sqlite_document_store import SQLiteDocumentStore
from nahida_bot.agent.storage.vector import (
    SQLiteVecIndex,
    reciprocal_rank_fusion,
)
from nahida_bot.db.engine import DatabaseEngine


# ---------------------------------------------------------------------------
# RRF weights
# ---------------------------------------------------------------------------


def test_rrf_default_weights_keep_historical_behavior() -> None:
    fused = reciprocal_rank_fusion([["a", "b"], ["c"]], limit=3)
    # Equal weights: rank-1 of each list ties at 1/61; insertion order wins.
    assert fused[0][0] == "a"
    assert fused[0][1] == pytest.approx(1.0 / 61.0)


def test_rrf_weighted_list_outranks_tied_unweighted_list() -> None:
    fused = reciprocal_rank_fusion(
        [["junk"], ["correct"]],
        weights=[0.6, 1.0],
        limit=2,
    )
    assert fused[0][0] == "correct"
    assert fused[0][1] == pytest.approx(1.0 / 61.0)
    assert fused[1][0] == "junk"
    assert fused[1][1] == pytest.approx(0.6 / 61.0)


def test_rrf_weighted_multi_membership_beats_single_membership() -> None:
    fused = reciprocal_rank_fusion(
        [["junk1", "both", "junk2"], ["both", "correct"]],
        weights=[0.6, 1.0],
        limit=3,
    )
    # "both" appears in the two lists (0.6/62 + 1/61) > "correct" (1/61)
    # > "junk1" (0.6/61) — agreement across channels still wins.
    assert [item for item, _score in fused] == ["both", "correct", "junk1"]


def test_rrf_rejects_mismatched_weights_length() -> None:
    with pytest.raises(ValueError, match="weights length"):
        reciprocal_rank_fusion([["a"], ["b"]], weights=[1.0])


# ---------------------------------------------------------------------------
# Shared helpers for store-level tests
# ---------------------------------------------------------------------------


class _StaticQueryProvider:
    """Embedding provider that returns a fixed vector for every query.

    Lets tests control the vector leg's ranking deterministically while the
    document vectors are planted via ``put_embedding``.
    """

    def __init__(self, query_vector: list[float]) -> None:
        self.provider_id = "static"
        self.model = "static-embed"
        self.dimensions = len(query_vector)
        self._query_vector = query_vector

    async def embed_texts(self, texts: list[str]) -> list[EmbeddingResult]:
        return [
            EmbeddingResult(
                embedding=list(self._query_vector),
                provider_id=self.provider_id,
                model=self.model,
            )
            for _ in texts
        ]


class _NoCallProvider:
    """Provider that fails the test if the embedding API is ever invoked."""

    def __init__(self, dimensions: int) -> None:
        self.provider_id = "local"
        self.model = "hash"
        self.dimensions = dimensions

    async def embed_texts(self, texts: list[str]) -> list[EmbeddingResult]:
        raise AssertionError("embed_texts must not be called during index self-heal")


async def _make_store() -> tuple[DatabaseEngine, SQLiteDocumentStore]:
    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    store = SQLiteDocumentStore(engine, collection="kbfix")
    await store.setup()
    return engine, store


# ---------------------------------------------------------------------------
# FTS: score direction + structural node exclusion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fts_scores_are_positive_descending() -> None:
    engine, store = await _make_store()
    try:
        await store.put(
            "weak",
            "some background text about unrelated topics",
            title="weak",
            node_type="passage",
        )
        await store.put(
            "strong",
            "nahida traveler attitude nahida traveler",
            title="strong",
            node_type="passage",
        )
        results = await store.search("nahida traveler attitude", limit=5)
        assert [r.doc_id for r in results] == ["strong"]
        assert results[0].score > 0
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_fts_and_tier_eliminates_keyword_collisions() -> None:
    """Multi-term queries require every term before falling back to OR.

    The collision doc matches one query term only; under the old OR-only
    form it ranked alongside the target. The AND tier must keep it out.
    """
    engine, store = await _make_store()
    try:
        await store.put(
            "target",
            "qiqi story chapter cold zombie apothecary",
            title="qiqi story",
            node_type="passage",
        )
        await store.put(
            "collision",
            "story about a completely different clerk",
            title="collision",
            node_type="passage",
        )
        # All terms present in target; collision lacks "qiqi".
        results = await store.search("qiqi story", limit=5)
        assert [r.doc_id for r in results] == ["target"]

        # A broad query no document fully satisfies falls back to the OR
        # form: partial matches return, ranked by term coverage (target
        # matches two terms, collision one).
        results = await store.search("qiqi story pharmacist", limit=5)
        assert results[0].doc_id == "target"
        assert {r.doc_id for r in results} == {"target", "collision"}
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_fts_title_match_outranks_body_match() -> None:
    engine, store = await _make_store()
    try:
        # Same term in body vs in title: the title hit must rank first
        # (bm25 column weights: title_index 3x content_index).
        await store.put(
            "body_hit",
            "goro mentions kokomi once in passing",
            title="unrelated diary",
            node_type="passage",
        )
        await store.put(
            "title_hit",
            "a short entry",
            title="kokomi notes",
            node_type="passage",
        )
        results = await store.search("kokomi", limit=5)
        assert results[0].doc_id == "title_hit"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_fts_excludes_title_only_structural_nodes() -> None:
    engine, store = await _make_store()
    try:
        # Structural nodes: content is only the heading / file name. Under
        # BM25 length normalization these dominated real passages in
        # production (30% of the Teyvat corpus).
        await store.put(
            "sec_junk",
            "nahida",
            title="nahida",
            node_type="section",
        )
        await store.put(
            "doc_junk",
            "nahida_5111",
            title="nahida_5111",
            node_type="document",
        )
        await store.put(
            "passage",
            "nahida speaks about the traveler attitude warmly",
            title="voice lines",
            node_type="passage",
        )
        results = await store.search("nahida", limit=10)
        assert [r.doc_id for r in results] == ["passage"]
    finally:
        await engine.close()


# ---------------------------------------------------------------------------
# Hybrid fusion: weights + candidate pool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hybrid_vector_rank1_outranks_fts_keyword_junk() -> None:
    """The core #49 scenario: FTS junk wins ties under equal-weight RRF.

    The correct document shares no query term (like 净善宫 lore vs an
    "五百年" keyword hit), so it gets zero FTS credit; the junk documents
    have no embeddings at all, matching production where keyword collisions
    sit far below the vector leg's ranking. The vector weight must let the
    semantic hit outrank the keyword junk even though each list contributes
    exactly one candidate.
    """
    engine, store = await _make_store()
    try:
        await store.put(
            "junk1",
            "nahida traveler attitude nahida traveler attitude",
            title="junk1",
            node_type="passage",
        )
        await store.put(
            "junk2",
            "nahida traveler attitude filler nahida",
            title="junk2",
            node_type="passage",
        )
        await store.put(
            "correct",
            "she watches the wanderer fondly, curious about the journey",
            title="correct",
            node_type="passage",
        )
        # Only the correct doc has a vector; it is aligned with the query.
        await store.put_embedding(
            "correct",
            [1.0, 0.0],
            provider_id="static",
            model="static-embed",
            content_hash="h3",
        )
        provider = _StaticQueryProvider([1.0, 0.0])

        results = await store.search_hybrid(
            "nahida traveler attitude", provider, limit=2
        )
        assert results[0].doc_id == "correct"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_hybrid_candidate_pool_reaches_beyond_fts_topk() -> None:
    """A correct doc at FTS rank > limit must still enter fusion.

    With limit=3 and five keyword-junk docs ahead of it in BM25, the old
    code fetched only the FTS top-3, so the correct doc never got any FTS
    credit; the amplified candidate pool now lets it accumulate both legs'
    scores and win.
    """
    engine, store = await _make_store()
    try:
        # One weak keyword mention keeps the correct doc inside the FTS
        # candidate pool while ranking below the keyword-stuffed junk.
        await store.put(
            "correct",
            "the wanderer's journey story, a quiet nahida mention only",
            title="correct",
            node_type="passage",
        )
        for i in range(5):
            await store.put(
                f"junk{i}",
                f"traveler attitude filler{i} " * 3,
                title=f"junk{i}",
                node_type="passage",
            )
        await store.put_embedding(
            "correct",
            [1.0, 0.0],
            provider_id="static",
            model="static-embed",
            content_hash="hc",
        )
        provider = _StaticQueryProvider([1.0, 0.0])

        fts_only = await store.search("traveler attitude", limit=3)
        assert "correct" not in [r.doc_id for r in fts_only]

        results = await store.search_hybrid(
            "traveler attitude nahida", provider, limit=3
        )
        assert results[0].doc_id == "correct"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_hybrid_vector_results_exclude_structural_nodes() -> None:
    engine, store = await _make_store()
    try:
        await store.put(
            "sec",
            "heading text",
            title="heading",
            node_type="section",
        )
        await store.put(
            "passage",
            "actual passage content here",
            title="passage",
            node_type="passage",
        )
        await store.put_embedding(
            "sec",
            [1.0, 0.0],
            provider_id="static",
            model="static-embed",
            content_hash="hs",
        )
        await store.put_embedding(
            "passage",
            [0.9, 0.1],
            provider_id="static",
            model="static-embed",
            content_hash="hp",
        )
        provider = _StaticQueryProvider([1.0, 0.0])

        results = await store.search_vector("anything", provider, limit=2)
        assert [r.doc_id for r in results] == ["passage"]
    finally:
        await engine.close()


# ---------------------------------------------------------------------------
# Vector-index self-heal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_documents_rebuilds_wiped_index_without_api_calls(tmp_path):
    engine = DatabaseEngine(str(tmp_path / "selfheal.sqlite3"))
    await engine.initialize()
    try:
        store = SQLiteDocumentStore(engine, collection="kbfix")
        await store.setup()
        await store.put("doc_1", "alpha content", title="one", node_type="passage")
        await store.put("doc_2", "beta content", title="two", node_type="passage")
        hash_provider = HashEmbeddingProvider(dimensions=8)
        index = SQLiteVecIndex(
            engine,
            dimensions=8,
            table_name="kbfix_embedding_vec",
            map_table="kbfix_vec_map",
        )
        await index.setup()

        first = await store.embed_documents(hash_provider, limit=10, vector_index=index)
        assert first.added == 2
        assert await index.count() == 2

        # Simulate the production incident: index tables wiped (or recreated
        # empty after a backend switch) while the JSON embeddings remain.
        async with engine.write_lock:
            await engine.execute("DELETE FROM kbfix_vec_map")
            await engine.execute("DELETE FROM kbfix_embedding_vec")
            await engine.db.commit()
        assert await index.count() == 0

        no_call = _NoCallProvider(dimensions=8)
        result = await store.embed_documents(no_call, limit=10, vector_index=index)

        assert result.added == 0
        assert result.needed == 0
        assert await index.count() == 2
        hits = await index.search([1.0] * 8, limit=10)
        assert {hit.item_id for hit in hits} == {"doc_1", "doc_2"}
    finally:
        await engine.close()


# ---------------------------------------------------------------------------
# kb auto-recall delegation
# ---------------------------------------------------------------------------


class _FakeManager:
    def __init__(self, collections: list[str]) -> None:
        self._collections = collections

    def list_collections(self) -> list[str]:
        return list(self._collections)

    def get(self, name: str):
        return SimpleNamespace(name=name)


def _fake_search_result(doc_id: str, score: float) -> SimpleNamespace:
    return SimpleNamespace(
        doc_id=doc_id,
        title=f"title-{doc_id}",
        content=f"content-{doc_id}",
        score=score,
        metadata={},
        path="",
        source_id="src",
        chunk_index=0,
        parent_id="",
        root_id="",
        node_type="passage",
    )


class _FakeKBPlugin:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []

    async def search_documents(self, collection: str, query: str, *, limit: int = 5):
        self.calls.append((collection, query, limit))
        return [_fake_search_result("best", 0.9), _fake_search_result("second", 0.5)]


def _make_runner(**overrides):
    from nahida_bot.core.session_runner import SessionRunner

    return SessionRunner(
        agent_loop=None,
        memory_store=None,
        provider_manager=None,
        model_router=None,
        workspace_manager=None,
        tool_registry=None,
        document_store_manager=overrides.pop(
            "document_store_manager", _FakeManager(["Teyvat"])
        ),
        kb_auto_recall_config=overrides.pop(
            "kb_auto_recall_config",
            SimpleNamespace(
                enabled=True, max_items=2, max_chars=2000, min_score=float("-inf")
            ),
        ),
        kb_plugin_resolver=overrides.pop("kb_plugin_resolver", None),
        **overrides,
    )


@pytest.mark.asyncio
async def test_auto_recall_delegates_to_kb_plugin_for_hybrid() -> None:
    plugin = _FakeKBPlugin()
    runner = _make_runner(kb_plugin_resolver=lambda: plugin)
    message = await runner._load_relevant_knowledge("纳西妲 对 旅行者 的态度")

    assert message is not None
    assert message.metadata["kb_backend"] == "hybrid"
    assert "content-best" in message.content
    assert plugin.calls == [("Teyvat", "纳西妲 对 旅行者 的态度", 2)]


@pytest.mark.asyncio
async def test_auto_recall_falls_back_to_fts_without_plugin() -> None:
    # A store-less fallback path: the fake manager's store namespace lacks
    # the search surface the adapter needs, so no context is produced — but
    # the resolver being None must route through the FTS branch, not raise.
    runner = _make_runner(kb_plugin_resolver=lambda: None)
    message = await runner._load_relevant_knowledge("纳西妲 对 旅行者 的态度")
    assert message is None


@pytest.mark.asyncio
async def test_auto_recall_hybrid_applies_min_score_threshold() -> None:
    plugin = _FakeKBPlugin()
    runner = _make_runner(
        kb_plugin_resolver=lambda: plugin,
        kb_auto_recall_config=SimpleNamespace(
            enabled=True, max_items=2, max_chars=2000, min_score=0.7
        ),
    )
    message = await runner._load_relevant_knowledge("some query")

    assert message is not None
    assert "content-best" in message.content
    assert "content-second" not in message.content
