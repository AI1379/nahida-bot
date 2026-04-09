"""Unit tests for memory models, keyword extraction, and SQLite memory store."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest

from nahida_bot.agent.memory import (
    ConversationTurn,
    MemoryRecord,
    SQLiteMemoryStore,
    extract_keywords,
)
from nahida_bot.agent.memory_store import MemoryStore
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
        """Chinese text should be segmented into meaningful words."""
        keywords = extract_keywords("今天天气很好，我想去公园散步")
        assert "天气" in keywords
        assert "公园" in keywords
        assert "散步" in keywords

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

    def test_chinese_empty_string(self) -> None:
        assert extract_keywords("") == []


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

    # Delete everything older than far future — should delete nothing.
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
