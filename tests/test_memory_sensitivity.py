"""Tests for Piece A — nearly-full soft-scope + sensitivity tag.

A0 (this file, foundation): the ``sensitivity_source`` provenance column
round-trips through store/read, and the new-item default is the soft ``public``.
Later sub-steps (A1 backfill, A2 retrieval filter, A3 dream tagging, A4 explicit
tag) add their own tests here.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest

from nahida_bot.agent.memory.consolidation import (
    MemoryConsolidator,
    classify_sensitivity,
)
from nahida_bot.agent.memory.sqlite import SQLiteMemoryStore
from nahida_bot.agent.memory.service import MemoryService
from nahida_bot.agent.retrieval.adapters import MemoryStoreRetrievalAdapter
from nahida_bot.agent.retrieval.models import RetrievalRequest, RetrievalScope
from nahida_bot.db.engine import DatabaseEngine


@pytest.fixture
async def store() -> AsyncGenerator[SQLiteMemoryStore, None]:
    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    s = SQLiteMemoryStore(engine)
    await s.ensure_session("test-session")
    yield s
    await engine.close()


@pytest.mark.asyncio
async def test_sensitivity_and_source_round_trip(store: SQLiteMemoryStore) -> None:
    """sensitivity + sensitivity_source persist and read back verbatim."""
    await store.append_item(
        content="a secret the user asked to keep here",
        title="private note",
        scope_type="chat",
        scope_id="chatA",
        kind="fact",
        sensitivity="private",
        sensitivity_source="explicit",
    )
    results = await store.search_items(
        "secret", scope_type="chat", scope_id="chatA", limit=10
    )
    assert results, "expected the stored item to be found"
    item = results[0]
    assert item.sensitivity == "private"
    assert item.sensitivity_source == "explicit"


@pytest.mark.asyncio
async def test_new_item_defaults_to_soft_public(store: SQLiteMemoryStore) -> None:
    """New items default to public/default — the 'nearly full soft' baseline."""
    await store.append_item(
        content="a normal recallable pineapple fact",
        title="ordinary fact",
        scope_type="chat",
        scope_id="chatA",
        kind="fact",
    )
    results = await store.search_items(
        "pineapple", scope_type="chat", scope_id="chatA", limit=10
    )
    assert results
    item = results[0]
    assert item.sensitivity == "public"
    assert item.sensitivity_source == "default"


# --- A3: consolidation auto-tagging -----------------------------------------


def test_classify_secret_signal_is_secret_like() -> None:
    # Unit test of the classifier in isolation. NOTE: the real consolidation
    # pipeline runs validate_memory_content() FIRST, which blocks English
    # secret markers (api_key/password/...) before classify_sensitivity is
    # reached — see test_consolidation_skips_english_secret_content below.
    sensitivity, source = classify_sensitivity("the api_key is abc123")
    assert sensitivity == "secret_like"
    assert source == "dream"


def test_classify_pii_is_private() -> None:
    sensitivity, source = classify_sensitivity("user mobile is 13800138000")
    assert sensitivity == "private"
    assert source == "dream"


def test_classify_privacy_marker_is_private() -> None:
    sensitivity, source = classify_sensitivity("这事别告诉群里其他人", title="私下说的")
    assert sensitivity == "private"
    assert source == "explicit"


def test_classify_explicit_privacy_beats_pii_dream() -> None:
    """An explicit privacy marker outranks inferred PII (Piece A4: explicit > dream)."""
    sensitivity, source = classify_sensitivity("别告诉别人,我的手机号是 13800138000")
    assert sensitivity == "private"
    assert source == "explicit"


def test_classify_secret_beats_explicit_privacy() -> None:
    """Secret signals (strictest) outrank explicit privacy markers."""
    sensitivity, source = classify_sensitivity("私下说,api_key 是 sk-abc123")
    assert sensitivity == "secret_like"
    assert source == "dream"


def test_classify_normal_content_is_public() -> None:
    sensitivity, source = classify_sensitivity(
        "user prefers Chinese for architecture discussion"
    )
    assert sensitivity == "public"
    assert source == "default"


# --- A3/A5: real consolidation path (not just the helper) --------------------


@pytest.mark.asyncio
async def test_consolidation_skips_english_secret_marked_content(
    store: SQLiteMemoryStore,
) -> None:
    """validate_memory_content blocks English secret markers BEFORE classify.

    Content like ``api_key`` is skipped by the dreaming pipeline's safety
    filter, so it is never stored as ``secret_like`` — the helper-level
    ``secret_like`` result does not apply on this path (review #5).
    """
    consolidator = MemoryConsolidator(store)
    applied = await consolidator.consolidate_turn(
        session_id="test-session",
        user_message="记住: the api_key is abc123",
        assistant_message="",
    )
    assert applied == 0


@pytest.mark.asyncio
async def test_consolidation_tags_secret_signal_that_passes_validation(
    store: SQLiteMemoryStore,
) -> None:
    """A secret signal not in _SECRET_MARKERS (e.g. Chinese 密钥) passes the
    safety filter and is tagged ``secret_like`` inside a typed chat scope."""
    consolidator = MemoryConsolidator(
        store,
        default_scope_type="chat",
        default_scope_id="milky:private:10001",
    )
    applied = await consolidator.consolidate_turn(
        session_id="milky:private:10001",
        user_message="记住: 这是数据库的密钥 千万别丢",
        assistant_message="",
    )
    assert applied >= 1
    items = await store.search_items(
        "", scope_type="chat", scope_id="milky:private:10001", limit=10
    )
    assert any(item.sensitivity == "secret_like" for item in items)


# --- A2: soft-scope retrieval filter ----------------------------------------


async def _seed_other_chat(store: SQLiteMemoryStore) -> dict[str, str]:
    """Seed another chat (chatB) with public/private/secret pineapple items."""
    await store.append_item(
        content="public alpha fact about pineapples ripening",
        title="alpha public",
        scope_type="chat",
        scope_id="chatB",
        kind="fact",
        sensitivity="public",
    )
    await store.append_item(
        content="private beta detail about pineapples origin",
        title="beta private",
        scope_type="chat",
        scope_id="chatB",
        kind="fact",
        sensitivity="private",
    )
    await store.append_item(
        content="secret gamma token about pineapples vault",
        title="gamma secret",
        scope_type="chat",
        scope_id="chatB",
        kind="fact",
        sensitivity="secret_like",
    )
    return {
        "public": "alpha public",
        "private": "beta private",
        "secret": "gamma secret",
    }


def _chat_a_request(query: str = "pineapples") -> RetrievalRequest:
    """A retrieval request scoped to chatA -> global (chatB is out of cascade)."""
    return RetrievalRequest(
        query=query,
        source_type="memory",
        scopes=(
            RetrievalScope("chat", "chatA"),
            RetrievalScope("global", "__global__"),
        ),
        limit=10,
    )


@pytest.mark.asyncio
async def test_soft_scope_off_excludes_cross_scope_items(
    store: SQLiteMemoryStore,
) -> None:
    """With soft_scope off, chatB items are never recalled from chatA."""
    titles = await _seed_other_chat(store)
    adapter = MemoryStoreRetrievalAdapter(memory_store=store, soft_scope=False)
    results = await adapter.retrieve(_chat_a_request())
    assert results == []
    assert titles["public"] not in {r.title for r in results}


@pytest.mark.asyncio
async def test_global_fallback_excludes_restricted_items(
    store: SQLiteMemoryStore,
) -> None:
    await store.append_item(
        content="public global pineapples guidance",
        title="public global",
        kind="procedure",
        sensitivity="public",
    )
    await store.append_item(
        content="private global pineapples legacy row",
        title="private global",
        kind="procedure",
        sensitivity="private",
        sensitivity_source="dream",
    )

    adapter = MemoryStoreRetrievalAdapter(memory_store=store, soft_scope=False)
    adapter_results = await adapter.retrieve(_chat_a_request())
    assert {item.title for item in adapter_results} == {"public global"}

    service = MemoryService(store)
    service_results = await service.search_items_cascade(
        "pineapples",
        ctx=None,
        session_id="milky:private:chatA",
        limit=10,
    )
    assert {item.title for item in service_results} == {"public global"}


@pytest.mark.asyncio
async def test_soft_scope_on_recalls_cross_scope_public(
    store: SQLiteMemoryStore,
) -> None:
    """With soft_scope on, chatB's PUBLIC item is recalled from chatA."""
    titles = await _seed_other_chat(store)
    adapter = MemoryStoreRetrievalAdapter(memory_store=store, soft_scope=True)
    results = await adapter.retrieve(_chat_a_request())
    result_titles = {r.title for r in results}
    assert titles["public"] in result_titles


@pytest.mark.asyncio
async def test_soft_scope_on_excludes_cross_scope_restricted(
    store: SQLiteMemoryStore,
) -> None:
    """Soft-scope never leaks private/secret_like across scopes (zero leak)."""
    titles = await _seed_other_chat(store)
    adapter = MemoryStoreRetrievalAdapter(memory_store=store, soft_scope=True)
    results = await adapter.retrieve(_chat_a_request())
    result_titles = {r.title for r in results}
    assert titles["private"] not in result_titles
    assert titles["secret"] not in result_titles


@pytest.mark.asyncio
async def test_soft_scope_dedups_in_scope_items(store: SQLiteMemoryStore) -> None:
    """An in-scope public item is not duplicated by the soft global pass."""
    await _seed_other_chat(store)
    await store.append_item(
        content="in-scope public fact about pineapples season",
        title="alpha in-scope",
        scope_type="chat",
        scope_id="chatA",
        kind="fact",
        sensitivity="public",
    )
    adapter = MemoryStoreRetrievalAdapter(memory_store=store, soft_scope=True)
    results = await adapter.retrieve(_chat_a_request())
    in_scope_hits = [r for r in results if r.title == "alpha in-scope"]
    assert len(in_scope_hits) == 1


@pytest.mark.asyncio
async def test_soft_scope_over_fetches_so_in_scope_public_does_not_starve(
    store: SQLiteMemoryStore,
) -> None:
    """Soft pass must over-fetch, else already-returned in-scope public items
    eat the budget and cross-scope public never surfaces (review starvation).

    The in-scope (chatA) item ranks #1 in the public pool (higher term
    frequency), so fetching only ``remaining`` would re-fetch it and dedupe to
    nothing. With a tight ``limit=2`` the chatB item must still come through.
    """
    await store.append_item(
        content="pineapples alpha note pineapples detail",
        title="alpha in-scope",
        scope_type="chat",
        scope_id="chatA",
        kind="fact",
        sensitivity="public",
    )
    await store.append_item(
        content="pineapples beta note other detail",
        title="beta cross-scope",
        scope_type="chat",
        scope_id="chatB",
        kind="fact",
        sensitivity="public",
    )
    adapter = MemoryStoreRetrievalAdapter(memory_store=store, soft_scope=True)
    results = await adapter.retrieve(
        RetrievalRequest(
            query="pineapples",
            source_type="memory",
            scopes=(
                RetrievalScope("chat", "chatA"),
                RetrievalScope("global", "__global__"),
            ),
            limit=2,
        )
    )
    titles = {r.title for r in results}
    assert "alpha in-scope" in titles
    assert "beta cross-scope" in titles  # starved without over-fetch
