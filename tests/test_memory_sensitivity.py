"""Tests for Piece A — nearly-full soft-scope + sensitivity tag.

A0 (this file, foundation): the ``sensitivity_source`` provenance column
round-trips through store/read, and the new-item default is the soft ``public``.
Later sub-steps (A1 backfill, A2 retrieval filter, A3 dream tagging, A4 explicit
tag) add their own tests here.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest

from nahida_bot.agent.memory.consolidation import classify_sensitivity
from nahida_bot.agent.memory.sqlite import SQLiteMemoryStore
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
    assert source == "dream"


def test_classify_normal_content_is_public() -> None:
    sensitivity, source = classify_sensitivity(
        "user prefers Chinese for architecture discussion"
    )
    assert sensitivity == "public"
    assert source == "default"
