"""Unit tests for memory models, keyword extraction, and SQLite memory store."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nahida_bot.agent.memory import (
    ConversationTurn,
    EmbeddingResult,
    HashEmbeddingProvider,
    MemoryConsolidator,
    MemoryItem,
    MemoryRecord,
    MemoryService,
    RuleBasedMemoryExtractor,
    RoutedEmbeddingProvider,
    SQLiteMemoryStore,
    StructuredMemoryStore,
    extract_keywords,
    parse_memory_dream,
    project_workspace_memory,
)
from nahida_bot.agent.context import ContextMessage
from nahida_bot.agent.memory.consolidation import build_dream_system_prompt
from nahida_bot.agent.memory.markdown import MEMORY_SUMMARY_FILE
from nahida_bot.agent.storage.tokenization import build_fts_query, tokenize_for_fts
from nahida_bot.agent.storage.vector import (
    VectorHit,
    VectorRecord,
    reciprocal_rank_fusion,
)
from nahida_bot.agent.providers.base import ProviderResponse
from nahida_bot.agent.memory.store import MemoryStore
from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.context import SessionContext
from nahida_bot.core.session_runner import SessionRunner
from nahida_bot.db.engine import DatabaseEngine


# ---------------------------------------------------------------------------
# Keyword extraction
# ---------------------------------------------------------------------------


class TestExtractKeywords:
    def test_extracts_lowercased_words(self) -> None:
        keywords = extract_keywords("Hello World Python")
        assert "hello" in keywords
        assert "world" in keywords
        assert "python" in keywords

    def test_filters_short_tokens(self) -> None:
        keywords = extract_keywords("I am a Python developer")
        assert "python" in keywords
        assert "developer" in keywords
        # Single-letter tokens are filtered by min_length=2 default.
        assert "a" not in keywords

    def test_deduplicates_stably(self) -> None:
        """Deduplication should preserve first-occurrence order."""
        keywords = extract_keywords("test value test value other")
        assert keywords == ["test", "value", "other"]

    def test_handles_empty_string(self) -> None:
        assert extract_keywords("") == []

    def test_handles_special_characters(self) -> None:
        keywords = extract_keywords("hello, world! foo-bar baz")
        assert "hello" in keywords
        assert "world" in keywords
        assert "foo" in keywords
        assert "bar" in keywords
        assert "baz" in keywords

    def test_order_is_stable(self) -> None:
        """Keywords should always appear in first-occurrence order."""
        k1 = extract_keywords("alpha beta gamma")
        k2 = extract_keywords("alpha beta gamma")
        assert k1 == k2
        assert k1 == ["alpha", "beta", "gamma"]


class TestExtractKeywordsCJK:
    """Tests for CJK (Chinese) keyword extraction via jieba."""

    def test_chinese_basic_segmentation(self) -> None:
        """Chinese text should produce non-empty CJK keywords."""
        keywords = extract_keywords("今天天气很好，我想去公园散步")
        assert keywords
        assert any(any("\u4e00" <= ch <= "\u9fff" for ch in kw) for kw in keywords)

    def test_chinese_single_word_query(self) -> None:
        """Single-word Chinese queries should produce at least one keyword."""
        keywords = extract_keywords("天气")
        assert "天气" in keywords

    def test_chinese_deduplication(self) -> None:
        """Repeated Chinese words should be deduplicated."""
        keywords = extract_keywords("天气天气天气")
        assert keywords.count("天气") == 1

    def test_mixed_chinese_english(self) -> None:
        """Mixed CJK + Latin text should extract keywords from both."""
        keywords = extract_keywords("使用Python编写爬虫程序")
        assert "python" in keywords
        assert "编写" in keywords or "程序" in keywords

    def test_chinese_search_mode_granularity(self) -> None:
        """jieba search mode should produce fine-grained tokens."""
        keywords = extract_keywords("南京市长江大桥")
        # search mode should decompose "南京市长江大桥" into sub-phrases.
        assert len(keywords) >= 2

    def test_chinese_punctuation_filtered(self) -> None:
        """Punctuation should not appear in keywords."""
        keywords = extract_keywords("你好，世界！")
        for kw in keywords:
            assert kw.isalnum() or all(c.isalnum() for c in kw)

    def test_short_chinese_tokens_filtered(self) -> None:
        """Single-character Chinese tokens should be filtered by min_length=2."""
        keywords = extract_keywords("我你在", min_length=2)
        assert "我" not in keywords
        assert "你" not in keywords


class TestFtsTokenization:
    def test_chinese_text_is_tokenized_for_sqlite_fts(self) -> None:
        index_text = tokenize_for_fts("记忆系统需要支持向量检索")
        assert " " in index_text
        assert "记忆" in index_text
        assert "检索" in index_text

    def test_fts_query_quotes_tokens(self) -> None:
        query = build_fts_query("向量检索")
        assert '"' in query
        assert "向量" in query or "检索" in query


# ---------------------------------------------------------------------------
# ConversationTurn
# ---------------------------------------------------------------------------


class TestConversationTurn:
    def test_creation_with_defaults(self) -> None:
        turn = ConversationTurn(role="user", content="hello")
        assert turn.role == "user"
        assert turn.content == "hello"
        assert turn.source == ""
        assert turn.metadata is None
        assert isinstance(turn.created_at, datetime)

    def test_creation_with_all_fields(self) -> None:
        now = datetime.now(UTC)
        turn = ConversationTurn(
            role="assistant",
            content="world",
            source="provider",
            metadata={"key": "value"},
            created_at=now,
        )
        assert turn.role == "assistant"
        assert turn.content == "world"
        assert turn.source == "provider"
        assert turn.metadata == {"key": "value"}
        assert turn.created_at == now

    def test_created_at_is_utc_aware(self) -> None:
        turn = ConversationTurn(role="user", content="hello")
        assert turn.created_at.tzinfo is not None


# ---------------------------------------------------------------------------
# MemoryStore is an ABC
# ---------------------------------------------------------------------------


class TestMemoryStoreABC:
    def test_memory_store_is_abstract(self) -> None:
        """MemoryStore should not be instantiable directly."""
        with pytest.raises(TypeError):
            MemoryStore()  # type: ignore[abstract]

    def test_memory_store_requires_ensure_session(self) -> None:
        """Contract should require ensure_session for all memory backends."""
        assert "ensure_session" in MemoryStore.__abstractmethods__


class FakeVectorIndex:
    """In-memory vector index for exercising the optional VectorIndex path."""

    def __init__(self) -> None:
        self.records: dict[str, VectorRecord] = {}

    async def upsert(self, records: list[VectorRecord]) -> None:
        for record in records:
            self.records[record.embedding_id] = record

    async def delete(self, ids: list[str]) -> None:
        for embedding_id in ids:
            self.records.pop(embedding_id, None)

    async def search(
        self, query_embedding: list[float], *, limit: int
    ) -> list[VectorHit]:
        return [
            VectorHit(item_id=record.item_id, score=1.0)
            for record in list(self.records.values())[:limit]
        ]


class FakeDreamProvider:
    """Provider stub for LLM memory dreaming tests."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.messages: list[ContextMessage] = []
        self.model: str | None = None

    async def chat(
        self,
        *,
        messages: list[ContextMessage],
        tools: list[object] | None = None,
        timeout_seconds: float | None = None,
        model: str | None = None,
    ) -> ProviderResponse:
        self.messages = messages
        self.model = model
        return ProviderResponse(content=self.content)


# ---------------------------------------------------------------------------
# SQLiteMemoryStore integration (in-memory)
# ---------------------------------------------------------------------------


@pytest.fixture
async def memory_store() -> AsyncGenerator[SQLiteMemoryStore, None]:
    """Create an in-memory SQLite memory store."""
    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    store = SQLiteMemoryStore(engine)
    await store.ensure_session("test-session")
    yield store
    await engine.close()


@pytest.mark.asyncio
async def test_append_and_get_recent(memory_store: SQLiteMemoryStore) -> None:
    turn1 = ConversationTurn(role="user", content="first message")
    turn2 = ConversationTurn(role="assistant", content="second message")

    await memory_store.append_turn("test-session", turn1)
    await memory_store.append_turn("test-session", turn2)

    recent = await memory_store.get_recent("test-session")
    assert len(recent) == 2
    assert recent[0].turn.content == "first message"
    assert recent[1].turn.content == "second message"
    assert recent[0].turn.role == "user"
    assert recent[1].turn.role == "assistant"


@pytest.mark.asyncio
async def test_get_recent_respects_limit(memory_store: SQLiteMemoryStore) -> None:
    for i in range(5):
        await memory_store.append_turn(
            "test-session", ConversationTurn(role="user", content=f"msg-{i}")
        )

    recent = await memory_store.get_recent("test-session", limit=3)
    assert len(recent) == 3
    assert recent[0].turn.content == "msg-2"
    assert recent[2].turn.content == "msg-4"


@pytest.mark.asyncio
async def test_search_by_keyword(memory_store: SQLiteMemoryStore) -> None:
    await memory_store.append_turn(
        "test-session",
        ConversationTurn(role="user", content="Tell me about Python programming"),
    )
    await memory_store.append_turn(
        "test-session",
        ConversationTurn(role="user", content="What is the weather today"),
    )

    results = await memory_store.search("test-session", "python")
    assert len(results) >= 1
    assert any("Python" in r.turn.content for r in results)


@pytest.mark.asyncio
async def test_search_by_multiple_keywords(memory_store: SQLiteMemoryStore) -> None:
    """Multi-keyword search should aggregate results (OR semantics)."""
    await memory_store.append_turn(
        "test-session",
        ConversationTurn(role="user", content="Python programming guide"),
    )
    await memory_store.append_turn(
        "test-session",
        ConversationTurn(role="user", content="Rust programming tutorial"),
    )
    await memory_store.append_turn(
        "test-session",
        ConversationTurn(role="user", content="Weather forecast today"),
    )

    results = await memory_store.search("test-session", "Python Rust")
    # Should match both the Python and Rust entries.
    assert len(results) >= 2
    contents = [r.turn.content for r in results]
    assert any("Python" in c for c in contents)
    assert any("Rust" in c for c in contents)


@pytest.mark.asyncio
async def test_search_falls_back_to_recent(memory_store: SQLiteMemoryStore) -> None:
    await memory_store.append_turn(
        "test-session",
        ConversationTurn(role="user", content="generic message"),
    )

    # Search with a keyword that won't match any indexed keyword.
    results = await memory_store.search("test-session", "xyznonexistent")
    # Fallback returns recent turns.
    assert len(results) >= 1


@pytest.mark.asyncio
async def test_evict_before(memory_store: SQLiteMemoryStore) -> None:
    await memory_store.append_turn(
        "test-session",
        ConversationTurn(role="user", content="old message"),
    )
    await memory_store.append_turn(
        "test-session",
        ConversationTurn(role="user", content="new message"),
    )

    # Delete everything older than a far-future cutoff: all records should be deleted.
    future = datetime.now(UTC) + timedelta(days=365)
    deleted = await memory_store.evict_before(future)
    assert deleted == 2

    recent = await memory_store.get_recent("test-session")
    assert len(recent) == 0


@pytest.mark.asyncio
async def test_evict_preserves_recent(memory_store: SQLiteMemoryStore) -> None:
    await memory_store.append_turn(
        "test-session",
        ConversationTurn(role="user", content="message to keep"),
    )

    # Cutoff in the past should preserve all records.
    past = datetime.now(UTC) - timedelta(days=1)
    deleted = await memory_store.evict_before(past)
    assert deleted == 0

    recent = await memory_store.get_recent("test-session")
    assert len(recent) == 1


@pytest.mark.asyncio
async def test_multiple_sessions_are_isolated() -> None:
    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    store = SQLiteMemoryStore(engine)
    await store.ensure_session("session-a")
    await store.ensure_session("session-b")

    await store.append_turn("session-a", ConversationTurn(role="user", content="alpha"))
    await store.append_turn("session-b", ConversationTurn(role="user", content="beta"))

    recent_a = await store.get_recent("session-a")
    recent_b = await store.get_recent("session-b")
    assert len(recent_a) == 1
    assert recent_a[0].turn.content == "alpha"
    assert len(recent_b) == 1
    assert recent_b[0].turn.content == "beta"

    await engine.close()


@pytest.mark.asyncio
async def test_memory_record_fields(memory_store: SQLiteMemoryStore) -> None:
    turn = ConversationTurn(
        role="assistant",
        content="response",
        source="provider",
        metadata={"finish_reason": "stop"},
    )
    await memory_store.append_turn("test-session", turn)

    records = await memory_store.get_recent("test-session")
    assert len(records) == 1
    record = records[0]
    assert isinstance(record, MemoryRecord)
    assert record.session_id == "test-session"
    assert record.turn.role == "assistant"
    assert record.turn.content == "response"
    assert record.turn.source == "provider"
    assert record.turn.metadata == {"finish_reason": "stop"}
    assert record.turn_id > 0


@pytest.mark.asyncio
async def test_keywords_are_backfilled_on_read(memory_store: SQLiteMemoryStore) -> None:
    """MemoryRecord.keywords should be populated when reading back turns."""
    await memory_store.append_turn(
        "test-session",
        ConversationTurn(role="user", content="Python programming tutorial"),
    )

    records = await memory_store.get_recent("test-session")
    assert len(records) == 1
    keywords = records[0].keywords
    assert "python" in keywords
    assert "programming" in keywords
    assert "tutorial" in keywords


@pytest.mark.asyncio
async def test_keywords_are_backfilled_on_search(
    memory_store: SQLiteMemoryStore,
) -> None:
    """Search results should also have keywords populated."""
    await memory_store.append_turn(
        "test-session",
        ConversationTurn(role="user", content="Python programming tutorial"),
    )

    results = await memory_store.search("test-session", "python")
    assert len(results) >= 1
    assert "python" in results[0].keywords


@pytest.mark.asyncio
async def test_append_and_search_memory_item_bm25(
    memory_store: SQLiteMemoryStore,
) -> None:
    item_id = await memory_store.append_item(
        title="memory design",
        content="Use SQLite FTS5 BM25 for durable memory search.",
        kind="decision",
    )

    results = await memory_store.search_items("BM25 memory")

    assert len(results) == 1
    assert isinstance(results[0], MemoryItem)
    assert results[0].item_id == item_id
    assert results[0].kind == "decision"
    assert "SQLite FTS5" in results[0].content


@pytest.mark.asyncio
async def test_chinese_memory_item_search_uses_pre_tokenized_fts(
    memory_store: SQLiteMemoryStore,
) -> None:
    await memory_store.append_item(
        title="中文检索",
        content="记忆系统需要支持中文向量检索和关键词召回。",
        kind="decision",
    )
    await memory_store.append_item(
        title="unrelated",
        content="Weather forecast and unrelated English text.",
    )

    results = await memory_store.search_items("中文检索")

    assert results
    assert "中文向量检索" in results[0].content


@pytest.mark.asyncio
async def test_memory_item_list_and_archive(memory_store: SQLiteMemoryStore) -> None:
    item_id = await memory_store.append_item(content="Remember this durable fact.")

    listed = await memory_store.search_items("")
    assert any(item.item_id == item_id for item in listed)

    archived = await memory_store.archive_item(item_id)
    assert archived is True
    listed_after = await memory_store.search_items("")
    assert all(item.item_id != item_id for item in listed_after)


@pytest.mark.asyncio
async def test_memory_item_embeddings_support_vector_search(
    memory_store: SQLiteMemoryStore,
) -> None:
    await memory_store.append_item(
        title="memory retrieval",
        content="Hybrid retrieval combines BM25 and embeddings.",
    )
    await memory_store.append_item(
        title="weather",
        content="Rain forecast for tomorrow.",
    )
    provider = HashEmbeddingProvider(dimensions=32)

    embedded_count = await memory_store.embed_items(provider)
    results = await memory_store.search_items_vector("BM25 embeddings", provider)

    assert embedded_count == 2
    assert results
    assert isinstance(results[0], MemoryItem)


@pytest.mark.asyncio
async def test_memory_item_embeddings_can_use_optional_vector_index(
    memory_store: SQLiteMemoryStore,
) -> None:
    item_id = await memory_store.append_item(
        title="sqlite vec",
        content="Optional sqlite-vec index can serve vector hits.",
    )
    provider = HashEmbeddingProvider(dimensions=32)
    vector_index = FakeVectorIndex()

    first_count = await memory_store.embed_items(provider, vector_index=vector_index)
    first_embedding_ids = set(vector_index.records)
    second_count = await memory_store.embed_items(provider, vector_index=vector_index)
    results = await memory_store.search_items_vector(
        "vector hits",
        provider,
        vector_index=vector_index,
    )

    assert first_count == 1
    # Second refresh is incremental: the item already has a current embedding,
    # so nothing is re-embedded (count 0) and no new vector records appear.
    assert second_count == 0
    assert set(vector_index.records) == first_embedding_ids
    assert results
    assert results[0].item_id == item_id


@pytest.mark.asyncio
async def test_embed_items_all_scopes_is_incremental_across_refreshes(
    memory_store: SQLiteMemoryStore,
) -> None:
    """A repeat embedding refresh skips items whose content is already embedded.

    Regression guard for the scheduled refresh re-embedding every item on every
    run: only new items should hit the provider.
    """
    await memory_store.append_item(title="first", content="Alice likes Python.")
    await memory_store.append_item(title="second", content="Bob prefers Rust.")
    provider = HashEmbeddingProvider(dimensions=32)

    first = await memory_store.embed_items_all_scopes(provider, limit=100)
    second = await memory_store.embed_items_all_scopes(provider, limit=100)

    assert first == 2
    assert second == 0  # nothing changed -> nothing re-embedded

    # A new item is embedded on the next refresh; existing ones stay skipped.
    await memory_store.append_item(title="third", content="Carol likes Go.")
    third = await memory_store.embed_items_all_scopes(provider, limit=100)
    assert third == 1


@pytest.mark.asyncio
async def test_memory_item_hybrid_search_fuses_fts_and_vector(
    memory_store: SQLiteMemoryStore,
) -> None:
    await memory_store.append_item(
        title="Chinese memory retrieval",
        content="中文记忆检索需要 BM25 和 embedding 混合召回。",
    )
    provider = HashEmbeddingProvider(dimensions=32)
    await memory_store.embed_items(provider)

    results = await memory_store.search_items_hybrid("中文 BM25", provider)

    assert results
    assert "中文记忆检索" in results[0].content


@pytest.mark.asyncio
async def test_memory_hybrid_search_uses_shared_candidate_pool_and_weights(
    memory_store: SQLiteMemoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await memory_store.append_item(
        item_id="fts-junk",
        title="keyword collision",
        content="keyword-stuffed but irrelevant",
    )
    await memory_store.append_item(
        item_id="vector-correct",
        title="semantic answer",
        content="the relevant memory",
    )
    junk, correct = await memory_store.get_items_by_ids(["fts-junk", "vector-correct"])
    search_limits: list[tuple[str, int]] = []

    async def fake_fts_search(
        query: str = "",
        *,
        scope_type: str | None = None,
        scope_id: str | None = None,
        limit: int = 10,
    ) -> list[MemoryItem]:
        del query, scope_type, scope_id
        search_limits.append(("fts", limit))
        return [junk]

    async def fake_vector_search(
        query: str,
        provider: object,
        *,
        scope_type: str,
        scope_id: str,
        limit: int,
        vector_index: object | None = None,
    ) -> list[MemoryItem]:
        del query, provider, scope_type, scope_id, vector_index
        search_limits.append(("vector", limit))
        return [correct]

    monkeypatch.setattr(memory_store, "search_items", fake_fts_search)
    monkeypatch.setattr(memory_store, "search_items_vector", fake_vector_search)

    results = await memory_store.search_items_hybrid(
        "ambiguous query",
        HashEmbeddingProvider(dimensions=2),
        limit=2,
    )

    assert search_limits == [("fts", 20), ("vector", 20)]
    assert [item.item_id for item in results] == ["vector-correct", "fts-junk"]


def test_reciprocal_rank_fusion_orders_shared_hits_first() -> None:
    fused = reciprocal_rank_fusion([["a", "b"], ["b", "a"]], limit=2)
    assert [item_id for item_id, _score in fused] == ["a", "b"]


class FakeRoutedProvider:
    async def embed_texts(
        self, texts: list[str], *, model: str | None = None
    ) -> list[EmbeddingResult]:
        assert model == "embed-model"
        return [
            EmbeddingResult(
                embedding=[float(index), 1.0],
                provider_id="raw-provider",
                model=model or "",
            )
            for index, _text in enumerate(texts)
        ]


@pytest.mark.asyncio
async def test_routed_embedding_provider_overrides_identity_and_batches() -> None:
    provider = RoutedEmbeddingProvider(
        FakeRoutedProvider(),
        provider_id="p1",
        model="embed-model",
        batch_size=1,
    )

    results = await provider.embed_texts(["a", "b"])

    assert [result.provider_id for result in results] == ["p1", "p1"]
    assert [result.model for result in results] == ["embed-model", "embed-model"]
    assert provider.dimensions == 2


def test_rule_based_memory_extractor_handles_chinese_explicit_memory() -> None:
    extractor = RuleBasedMemoryExtractor()

    results = extractor.extract(
        session_id="s1",
        user_message="请记住：我喜欢你默认用中文回答，并且说明关键取舍。",
        assistant_message="好的。",
    )

    assert len(results) == 1
    assert results[0].content == "我喜欢你默认用中文回答，并且说明关键取舍。"


def test_parse_memory_dream_accepts_fenced_json() -> None:
    dream = parse_memory_dream(
        """```json
        {
          "add": [
            {
              "kind": "preference",
              "title": "语言偏好",
              "content": "用户偏好用中文讨论技术实现。",
              "confidence": 0.9,
              "importance": 0.8,
              "evidence": "用户要求中文交互。"
            }
          ],
          "archive": [
            {"item_id": "mem_old", "reason": "被新的偏好替代。"}
          ]
        }
        ```"""
    )

    assert dream.additions[0].kind == "preference"
    assert dream.additions[0].content == "用户偏好用中文讨论技术实现。"
    assert dream.additions[0].audience == "current"
    assert dream.archives[0].item_id == "mem_old"


def test_parse_memory_dream_normalizes_audience_and_keeps_summary_current() -> None:
    dream = parse_memory_dream(
        """{
          "add": [
            {
              "kind": "decision",
              "audience": "global",
              "title": "bot-wide rule",
              "content": "This public rule applies to every chat."
            },
            {
              "kind": "summary",
              "audience": "global",
              "title": "chat recap",
              "content": "This is only a current-chat summary."
            }
          ],
          "archive": []
        }"""
    )

    assert dream.additions[0].audience == "global"
    assert dream.additions[1].audience == "current"


def test_dream_system_prompt_uses_app_name_without_language_hardcoding() -> None:
    prompt = build_dream_system_prompt("Custom Assistant")

    assert "Custom Assistant" in prompt
    assert "Nahida Bot" not in prompt
    assert "Prefer concise Chinese" not in prompt
    assert "language and terminology the user normally uses" in prompt


@pytest.mark.asyncio
async def test_memory_consolidator_auto_applies_and_projects_workspace(
    memory_store: SQLiteMemoryStore,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "MEMORY.md").write_text(
        "# Memory\n\n## Manual\n", encoding="utf-8"
    )
    consolidator = MemoryConsolidator(memory_store)

    applied = await consolidator.consolidate_turn(
        session_id="test-session",
        user_message="请记住：我喜欢你默认用中文回答，并且说明关键取舍。",
        assistant_message="好的，我会记住。",
        workspace_id="default",
        workspace_root=workspace_root,
    )

    items = await memory_store.search_items("中文回答")
    candidates = await memory_store.list_candidates(status="auto_applied")
    summary = (workspace_root / MEMORY_SUMMARY_FILE).read_text(encoding="utf-8")
    memory_md = (workspace_root / "MEMORY.md").read_text(encoding="utf-8")

    assert applied == 1
    assert items
    assert candidates
    assert "中文回答" in summary
    assert "nahida-memory-generated:start" in memory_md
    assert "## Manual" in memory_md


@pytest.mark.asyncio
async def test_projection_filters_out_restricted_items(
    memory_store: SQLiteMemoryStore,
    tmp_path: Path,
) -> None:
    """The Markdown projection is sensitivity-filtered (Path B leak fix).

    Only ``sensitivity='public'`` items reach the derived ``MEMORY.md`` /
    ``memory_summary.md`` files so the agent's grep-fallback recall can never
    surface private/secret_like items in another chat (the leak class fixed in
    96860d7, now structurally impossible at the projection source).
    """
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    await memory_store.append_item(
        title="public fact",
        content="a public recallable fact",
        kind="fact",
        sensitivity="public",
    )
    await memory_store.append_item(
        title="secret",
        content="a secret value never to leak",
        kind="fact",
        sensitivity="secret_like",
        sensitivity_source="explicit",
    )
    await memory_store.append_item(
        title="private note",
        content="a private between-us note",
        kind="fact",
        sensitivity="private",
        sensitivity_source="explicit",
    )

    await project_workspace_memory(memory_store, workspace_root)

    summary = (workspace_root / MEMORY_SUMMARY_FILE).read_text(encoding="utf-8")
    memory_md = (workspace_root / "MEMORY.md").read_text(encoding="utf-8")

    assert "a public recallable fact" in summary
    assert "a public recallable fact" in memory_md
    # Restricted items must never enter the grep-fallback projection files.
    assert "a secret value never to leak" not in summary
    assert "a secret value never to leak" not in memory_md
    assert "a private between-us note" not in summary
    assert "a private between-us note" not in memory_md


def test_sqlite_store_satisfies_structured_protocol(
    memory_store: SQLiteMemoryStore,
) -> None:
    """SQLiteMemoryStore structurally satisfies StructuredMemoryStore."""
    assert isinstance(memory_store, StructuredMemoryStore)


@pytest.mark.asyncio
async def test_memory_service_search_cascade_dedups_and_cascades(
    memory_store: SQLiteMemoryStore,
) -> None:
    """MemoryService.search_items_cascade resolves scopes and dedups by id."""
    await memory_store.append_item(
        title="lang",
        content="user prefers Chinese",
        kind="preference",
        scope_type="global",
        scope_id="__global__",
    )
    service = MemoryService(memory_store, soft_scope=False)
    # No session context -> global-only cascade per identity policy.
    items = await service.search_items_cascade("Chinese", ctx=None, limit=5)
    assert items and items[0].content == "user prefers Chinese"
    # Repeated scope entries in the cascade do not duplicate results.
    assert len({item.item_id for item in items}) == len(items)


@pytest.mark.asyncio
async def test_memory_service_updates_and_archives_only_visible_items(
    memory_store: SQLiteMemoryStore,
) -> None:
    session_id = "milky:private:10001"
    old_id = await memory_store.append_item(
        title="old preference",
        content="User prefers verbose answers.",
        kind="preference",
        scope_type="chat",
        scope_id=session_id,
    )
    foreign_id = await memory_store.append_item(
        title="foreign",
        content="Another chat's private fact.",
        kind="fact",
        scope_type="chat",
        scope_id="milky:private:20002",
    )
    service = MemoryService(memory_store)

    replacement_id = await service.update_item_for_context(
        old_id,
        "User prefers concise answers.",
        key="answer preference",
        ctx=None,
        session_id=session_id,
        metadata={"kind": "preference"},
    )

    assert replacement_id is not None
    assert await memory_store.get_items_by_ids([old_id]) == []
    replacements = await memory_store.get_items_by_ids([replacement_id])
    assert replacements[0].scope_type == "chat"
    assert replacements[0].scope_id == session_id
    assert replacements[0].metadata["replaces_item_id"] == old_id
    assert not await service.archive_item_for_context(
        foreign_id, ctx=None, session_id=session_id
    )
    assert await service.archive_item_for_context(
        replacement_id, ctx=None, session_id=session_id
    )


@pytest.mark.asyncio
async def test_memory_service_update_promotes_and_demotes_scope(
    memory_store: SQLiteMemoryStore,
) -> None:
    session_id = "milky:private:10001"
    service = MemoryService(memory_store)

    chat_id = await service.store_item(
        "public chat fact",
        "This is a public fact scoped to the current chat.",
        session_id=session_id,
        metadata={"kind": "fact", "audience": "current"},
    )
    by_id = await memory_store.get_items_by_ids([chat_id])
    assert by_id[0].scope_type == "chat"

    promoted_id = await service.update_item_for_context(
        chat_id,
        "This fact now applies bot-wide.",
        ctx=None,
        session_id=session_id,
        metadata={"kind": "fact", "audience": "global"},
    )
    assert promoted_id is not None
    assert await memory_store.get_items_by_ids([chat_id]) == []
    promoted = await memory_store.get_items_by_ids([promoted_id])
    assert promoted[0].scope_type == "global"
    assert promoted[0].scope_id == "__global__"
    assert promoted[0].metadata["replaces_item_id"] == chat_id
    assert promoted[0].metadata["audience"] == "global"

    demoted_id = await service.update_item_for_context(
        promoted_id,
        "This fact belongs only to the current chat now.",
        ctx=None,
        session_id=session_id,
        metadata={"kind": "fact", "audience": "current"},
    )
    assert demoted_id is not None
    demoted = await memory_store.get_items_by_ids([demoted_id])
    assert demoted[0].scope_type == "chat"
    assert demoted[0].scope_id == session_id
    assert demoted[0].metadata["replaces_item_id"] == promoted_id
    assert demoted[0].metadata["audience"] == "current"


@pytest.mark.asyncio
async def test_memory_service_update_reassigns_to_target_scope(
    memory_store: SQLiteMemoryStore,
) -> None:
    """Target scope override can reassign a memory to a specific person/account."""
    session_id = "milky:private:10001"
    service = MemoryService(memory_store)

    global_id = await service.store_item(
        "stale global decision",
        "This was accidentally written to global.",
        session_id=session_id,
        metadata={"kind": "decision", "audience": "global"},
    )
    by_id = await memory_store.get_items_by_ids([global_id])
    assert by_id[0].scope_type == "global"

    reassigned_id = await service.update_item_for_context(
        global_id,
        "This decision belongs to person owner.",
        ctx=None,
        session_id=session_id,
        metadata={
            "kind": "decision",
            "target_scope_type": "person",
            "target_scope_id": "owner",
        },
    )
    assert reassigned_id is not None
    assert await memory_store.get_items_by_ids([global_id]) == []
    reassigned = await memory_store.get_items_by_ids([reassigned_id])
    assert reassigned[0].scope_type == "person"
    assert reassigned[0].scope_id == "owner"
    assert reassigned[0].metadata["replaces_item_id"] == global_id


@pytest.mark.asyncio
async def test_memory_service_requires_explicit_public_global_audience(
    memory_store: SQLiteMemoryStore,
) -> None:
    session_id = "milky:private:10001"
    service = MemoryService(memory_store)

    local_decision_id = await service.store_item(
        "local decision",
        "This decision belongs to the current project chat.",
        session_id=session_id,
        metadata={"kind": "decision"},
    )
    global_decision_id = await service.store_item(
        "bot-wide decision",
        "This public decision intentionally applies across every chat.",
        session_id=session_id,
        metadata={"kind": "decision", "audience": "global"},
    )
    local_summary_id = await service.store_item(
        "chat summary",
        "This summary remains local even if global was requested.",
        session_id=session_id,
        metadata={"kind": "summary", "audience": "global"},
    )
    private_id = await service.store_item(
        "private fact",
        "This private fact must remain in the current scope.",
        session_id=session_id,
        metadata={
            "kind": "fact",
            "audience": "global",
            "sensitivity": "private",
        },
    )

    by_id = {
        item.item_id: item
        for item in await memory_store.get_items_by_ids(
            [local_decision_id, global_decision_id, local_summary_id, private_id]
        )
    }
    assert by_id[local_decision_id].scope_type == "chat"
    assert by_id[global_decision_id].scope_type == "global"
    assert by_id[local_summary_id].scope_type == "chat"
    assert by_id[private_id].scope_type == "chat"


@pytest.mark.asyncio
async def test_memory_service_routes_non_portable_fact_to_current_chat(
    memory_store: SQLiteMemoryStore,
) -> None:
    service = MemoryService(memory_store)
    group_chat = "milky:group:20001"

    item_id = await service.store_item(
        "group-local nickname",
        "People in this group call the current sender Old Wang.",
        session_id=group_chat,
        metadata={
            "kind": "fact",
            "audience": "global",
            "sensitivity": "public",
            "portable": False,
        },
    )

    items = await memory_store.get_items_by_ids([item_id])
    assert len(items) == 1
    assert items[0].scope_type == "chat"
    assert items[0].scope_id == group_chat
    assert items[0].metadata["portable"] is False
    assert items[0].metadata["audience"] == "current"

    promoted = await service.update_item_for_context(
        item_id,
        "This nickname should still not become bot-wide.",
        ctx=None,
        session_id=group_chat,
        metadata={"audience": "global"},
    )
    assert promoted is None
    assert len(await memory_store.get_items_by_ids([item_id])) == 1

    ctx = SessionContext(
        platform="milky",
        chat_id="20001",
        session_id=group_chat,
        chat_address=ChatAddress(
            channel="milky", target_type="group", target_id="20001"
        ),
        person_id="owner",
        sender_account_key="milky:user:10001",
    )
    replacement_id = await service.update_item_for_context(
        item_id,
        "People in this group still use the same nickname.",
        ctx=ctx,
        session_id=group_chat,
        metadata={"audience": "current"},
    )
    assert replacement_id is not None
    replacement = await memory_store.get_items_by_ids([replacement_id])
    assert replacement[0].scope_type == "chat"
    assert replacement[0].scope_id == group_chat


@pytest.mark.asyncio
async def test_session_runner_can_disable_rule_based_consolidation(
    memory_store: SQLiteMemoryStore,
    tmp_path: Path,
) -> None:
    runner = SessionRunner(
        memory_store=memory_store,
        memory_consolidation_rule_based_enabled=False,
    )

    await runner._consolidate_memory_after_turn(
        session_id="test-session",
        user_message="请记住：我喜欢你默认用中文回答，并且说明关键取舍。",
        assistant_message="好的，我会记住。",
        workspace_id="default",
        workspace_root=tmp_path,
    )

    assert await memory_store.search_items("中文回答") == []
    assert not (tmp_path / MEMORY_SUMMARY_FILE).exists()


@pytest.mark.asyncio
async def test_memory_consolidator_applies_llm_dream_add_and_archive(
    memory_store: SQLiteMemoryStore,
    tmp_path: Path,
) -> None:
    old_id = await memory_store.append_item(
        title="旧语言偏好",
        content="用户偏好英文回答。",
        kind="preference",
    )
    provider = FakeDreamProvider(
        f"""{{
          "add": [
            {{
              "kind": "preference",
              "title": "语言偏好",
              "content": "用户偏好用中文讨论技术实现。",
              "confidence": 0.92,
              "importance": 0.8,
              "evidence": "用户要求用中文讨论记忆系统。"
            }}
          ],
          "archive": [
            {{"item_id": "{old_id}", "reason": "新的对话明确改为中文偏好。"}}
          ]
        }}"""
    )
    consolidator = MemoryConsolidator(memory_store)

    applied = await consolidator.consolidate_turn(
        session_id="test-session",
        user_message="我们之后都用中文讨论这个项目。",
        assistant_message="好的，之后默认用中文讨论。",
        workspace_root=tmp_path,
        dream_provider=provider,
        dream_model="memory-model",
    )

    new_items = await memory_store.search_items("中文讨论")
    old_items = await memory_store.search_items("英文回答")
    candidates = await memory_store.list_candidates(status="auto_applied")

    assert provider.model == "memory-model"
    assert applied >= 2
    assert any(item.content == "用户偏好用中文讨论技术实现。" for item in new_items)
    assert all(item.item_id != old_id for item in old_items)
    assert any(candidate.kind == "archive" for candidate in candidates)
