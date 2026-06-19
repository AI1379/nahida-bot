"""Tests for memory scope isolation (chat vs global).

Covers scope resolution, store-level isolation, consolidator scope-aware writes
(including the per-kind override and legacy backward compatibility), scope-aware
deduplication, the cross-scope embedding refresh, and the read-path cascade.
See ``docs/design/memory-scoping.md``.
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from nahida_bot.agent.memory import (
    HashEmbeddingProvider,
    MemoryConsolidator,
    SQLiteMemoryStore,
)
from nahida_bot.agent.memory.scope import (
    resolve_scope_from_session,
    scope_for_kind,
)
from nahida_bot.agent.providers.base import ProviderResponse
from nahida_bot.core.session_runner import SessionRunner
from nahida_bot.db.engine import DatabaseEngine


@pytest.fixture
async def memory_store() -> Any:
    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    store = SQLiteMemoryStore(engine)
    yield store
    await engine.close()


# ---------------------------------------------------------------------------
# resolve_scope_from_session
# ---------------------------------------------------------------------------


class TestResolveScopeFromSession:
    def test_typed_private_chat_resolves_to_chat(self) -> None:
        assert resolve_scope_from_session("milky:private:10001") == (
            "chat",
            "milky:private:10001",
        )

    def test_typed_group_resolves_to_chat(self) -> None:
        assert resolve_scope_from_session("milky:group:20001") == (
            "chat",
            "milky:group:20001",
        )

    def test_cron_derived_typed_session_resolves_to_chat(self) -> None:
        assert resolve_scope_from_session("milky:private:10001:cron:abc") == (
            "chat",
            "milky:private:10001",
        )

    def test_legacy_two_segment_session_resolves_to_global(self) -> None:
        assert resolve_scope_from_session("milky:10001") == ("global", "__global__")

    def test_empty_session_resolves_to_global(self) -> None:
        assert resolve_scope_from_session("") == ("global", "__global__")

    def test_malformed_session_does_not_raise(self) -> None:
        # A bad session id must never raise — consolidation has to stay robust.
        assert resolve_scope_from_session("::garbage::") == ("global", "__global__")
        assert resolve_scope_from_session("not-a-session") == (
            "global",
            "__global__",
        )


class TestScopeForKind:
    def test_personal_kinds_are_chat_scoped(self) -> None:
        for kind in ("preference", "fact", "task"):
            assert scope_for_kind(kind) == "chat"

    def test_shared_kinds_are_global_scoped(self) -> None:
        for kind in ("decision", "procedure", "warning", "summary"):
            assert scope_for_kind(kind) == "global"


# ---------------------------------------------------------------------------
# Store-level isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_search_isolates_chat_scopes(memory_store: Any) -> None:
    """A chat-scoped item must not surface in another chat's scope search."""
    await memory_store.append_item(
        content="Alice likes Python",
        scope_type="chat",
        scope_id="milky:private:10001",
        kind="preference",
        title="lang",
    )

    alice_hits = await memory_store.search_items(
        "Python", scope_type="chat", scope_id="milky:private:10001", limit=10
    )
    bob_hits = await memory_store.search_items(
        "Python", scope_type="chat", scope_id="milky:private:10002", limit=10
    )
    global_hits = await memory_store.search_items(
        "Python", scope_type="global", scope_id="__global__", limit=10
    )

    assert any("Alice" in h.content for h in alice_hits)
    assert not any("Alice" in h.content for h in bob_hits)
    assert not any("Alice" in h.content for h in global_hits)


# ---------------------------------------------------------------------------
# Consolidator scope-aware writes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consolidator_writes_preference_to_chat_scope(memory_store: Any) -> None:
    """A typed chat session must file personal memory under its chat scope."""
    consolidator = MemoryConsolidator(
        memory_store,
        default_scope_type="chat",
        default_scope_id="milky:private:10001",
    )
    applied = await consolidator.consolidate_turn(
        session_id="milky:private:10001",
        user_message="我喜欢吃辣的食物，每顿都要有辣椒",
    )
    assert applied >= 1

    chat_hits = await memory_store.search_items(
        "", scope_type="chat", scope_id="milky:private:10001", limit=10
    )
    assert any("吃辣" in h.content for h in chat_hits)
    global_hits = await memory_store.search_items(
        "", scope_type="global", scope_id="__global__", limit=10
    )
    assert not any("吃辣" in h.content for h in global_hits)


@pytest.mark.asyncio
async def test_consolidator_per_kind_keeps_decisions_global(memory_store: Any) -> None:
    """Within a chat session, shared kinds still land in global scope."""
    consolidator = MemoryConsolidator(
        memory_store,
        default_scope_type="chat",
        default_scope_id="milky:private:10001",
    )
    await consolidator.consolidate_turn(
        session_id="milky:private:10001",
        user_message="项目决定使用 Python 作为主语言",
    )

    global_hits = await memory_store.search_items(
        "Python", scope_type="global", scope_id="__global__", limit=10
    )
    assert any("Python" in h.content and h.kind == "decision" for h in global_hits)


@pytest.mark.asyncio
async def test_consolidator_legacy_session_stays_global(memory_store: Any) -> None:
    """A legacy (untyped) session must keep writing to global — backward compat."""
    consolidator = MemoryConsolidator(memory_store)  # default scope = global
    await consolidator.consolidate_turn(
        session_id="legacy-session",
        user_message="我喜欢吃辣的食物，每顿都要有辣椒",
    )
    global_hits = await memory_store.search_items(
        "", scope_type="global", scope_id="__global__", limit=10
    )
    assert any("吃辣" in h.content for h in global_hits)


@pytest.mark.asyncio
async def test_dedup_is_scope_scoped(memory_store: Any) -> None:
    """A duplicate is skipped within the same chat but written across chats."""
    consolidator_a = MemoryConsolidator(
        memory_store,
        default_scope_type="chat",
        default_scope_id="milky:private:10001",
    )
    await consolidator_a.consolidate_turn(
        session_id="milky:private:10001", user_message="I prefer spicy food"
    )
    # Same preference again in the same chat → deduped (no new item).
    second = await consolidator_a.consolidate_turn(
        session_id="milky:private:10001", user_message="I prefer spicy food"
    )
    assert second == 0

    # Same preference in a different chat → not a duplicate → written.
    consolidator_b = MemoryConsolidator(
        memory_store,
        default_scope_type="chat",
        default_scope_id="milky:private:10002",
    )
    third = await consolidator_b.consolidate_turn(
        session_id="milky:private:10002", user_message="I prefer spicy food"
    )
    assert third >= 1


# ---------------------------------------------------------------------------
# Identity-aware write scope (issue #7, Phase 3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consolidator_writes_personal_to_person_scope_when_linked(
    memory_store: Any,
) -> None:
    """A linked sender's personal memory lands in person scope, not chat/account."""
    consolidator = MemoryConsolidator(
        memory_store,
        default_scope_type="chat",
        default_scope_id="milky:private:10001",
    )
    applied = await consolidator.consolidate_turn(
        session_id="milky:private:10001",
        user_message="我喜欢吃辣的食物，每顿都要有辣椒",
        person_id="owner",
        sender_account_key="milky:user:10001",
    )
    assert applied >= 1

    person_hits = await memory_store.search_items(
        "", scope_type="person", scope_id="owner", limit=10
    )
    assert any("吃辣" in h.content for h in person_hits)
    chat_hits = await memory_store.search_items(
        "", scope_type="chat", scope_id="milky:private:10001", limit=10
    )
    assert not any("吃辣" in h.content for h in chat_hits)
    account_hits = await memory_store.search_items(
        "", scope_type="account", scope_id="milky:user:10001", limit=10
    )
    assert not any("吃辣" in h.content for h in account_hits)


@pytest.mark.asyncio
async def test_consolidator_writes_personal_to_account_scope_when_unlinked(
    memory_store: Any,
) -> None:
    """An unlinked sender's personal memory lands in account scope, not chat."""
    consolidator = MemoryConsolidator(
        memory_store,
        default_scope_type="chat",
        default_scope_id="milky:private:10001",
    )
    applied = await consolidator.consolidate_turn(
        session_id="milky:private:10001",
        user_message="我喜欢吃辣的食物，每顿都要有辣椒",
        sender_account_key="milky:user:10001",
    )
    assert applied >= 1

    account_hits = await memory_store.search_items(
        "", scope_type="account", scope_id="milky:user:10001", limit=10
    )
    assert any("吃辣" in h.content for h in account_hits)
    chat_hits = await memory_store.search_items(
        "", scope_type="chat", scope_id="milky:private:10001", limit=10
    )
    assert not any("吃辣" in h.content for h in chat_hits)


@pytest.mark.asyncio
async def test_consolidator_global_kind_stays_global_when_linked(
    memory_store: Any,
) -> None:
    """A linked sender's shared decision still lands in global scope."""
    consolidator = MemoryConsolidator(
        memory_store,
        default_scope_type="chat",
        default_scope_id="milky:private:10001",
    )
    await consolidator.consolidate_turn(
        session_id="milky:private:10001",
        user_message="项目决定使用 Python 作为主语言",
        person_id="owner",
    )

    global_hits = await memory_store.search_items(
        "Python", scope_type="global", scope_id="__global__", limit=10
    )
    assert any("Python" in h.content and h.kind == "decision" for h in global_hits)
    person_hits = await memory_store.search_items(
        "Python", scope_type="person", scope_id="owner", limit=10
    )
    assert not any(h.kind == "decision" for h in person_hits)


@pytest.mark.asyncio
async def test_consolidator_dedup_is_person_scoped(memory_store: Any) -> None:
    """A duplicate is deduped within a person; independent across persons."""
    consolidator = MemoryConsolidator(
        memory_store,
        default_scope_type="chat",
        default_scope_id="milky:private:10001",
    )
    first = await consolidator.consolidate_turn(
        session_id="milky:private:10001",
        user_message="I prefer spicy food",
        person_id="owner",
    )
    assert first >= 1
    # Same preference, same person → deduped.
    second = await consolidator.consolidate_turn(
        session_id="milky:private:10001",
        user_message="I prefer spicy food",
        person_id="owner",
    )
    assert second == 0

    # Same preference, different person → not a duplicate → written.
    consolidator_b = MemoryConsolidator(
        memory_store,
        default_scope_type="chat",
        default_scope_id="milky:private:10002",
    )
    third = await consolidator_b.consolidate_turn(
        session_id="milky:private:10002",
        user_message="I prefer spicy food",
        person_id="bob",
    )
    assert third >= 1


# ---------------------------------------------------------------------------
# Cross-scope embedding refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_items_all_scopes_covers_chat_and_global(memory_store: Any) -> None:
    await memory_store.append_item(
        content="alice preference",
        scope_type="chat",
        scope_id="milky:private:10001",
        kind="preference",
        title="a",
    )
    await memory_store.append_item(
        content="global decision",
        scope_type="global",
        scope_id="__global__",
        kind="decision",
        title="g",
    )
    provider = HashEmbeddingProvider(dimensions=32)
    count = await memory_store.embed_items_all_scopes(provider, limit=100)
    assert count == 2


# ---------------------------------------------------------------------------
# Read-path cascade via SessionRunner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_relevant_memory_cascade_isolates_chats(memory_store: Any) -> None:
    """A typed session recalls its own chat items plus global, not other chats."""
    await memory_store.append_item(
        content="Alice likes Python",
        scope_type="chat",
        scope_id="milky:private:10001",
        kind="preference",
        title="lang",
    )
    # Bob's item also matches the query "Python" but lives in another chat scope,
    # so isolation (not the query) is what must keep it out of Alice's context.
    await memory_store.append_item(
        content="Bob likes Python too",
        scope_type="chat",
        scope_id="milky:private:10002",
        kind="preference",
        title="lang",
    )
    await memory_store.append_item(
        content="Project uses Python everywhere",
        scope_type="global",
        scope_id="__global__",
        kind="decision",
        title="stack",
    )

    runner = SessionRunner(memory_store=cast(Any, memory_store))
    message = await runner._load_relevant_memory(
        "Python", session_id="milky:private:10001"
    )

    assert message is not None
    assert "Alice" in message.content
    assert "Project uses Python" in message.content  # global cascades in
    assert "Bob" not in message.content  # other chat isolated out


# ---------------------------------------------------------------------------
# Dreaming isolation
# ---------------------------------------------------------------------------


class _FakeDreamProvider:
    """Minimal provider stub returning a fixed dreaming JSON response."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.model: str | None = None

    async def chat(
        self,
        *,
        messages: list[object],
        tools: list[object] | None = None,
        timeout_seconds: float | None = None,
        model: str | None = None,
    ) -> ProviderResponse:
        self.model = model
        return ProviderResponse(content=self.content)


@pytest.mark.asyncio
async def test_dreaming_does_not_archive_other_chat_items(
    memory_store: Any,
) -> None:
    """Session A's dreaming must not archive session B's chat-scoped items.

    The dreamer only sees the current session's scope; archive requests for
    item ids outside that scope are ignored, so cross-chat data stays safe even
    if the model hallucinates a foreign item id.
    """
    item_a = await memory_store.append_item(
        content="Alice prefers Python",
        scope_type="chat",
        scope_id="milky:private:10001",
        kind="preference",
        title="lang",
    )
    item_b = await memory_store.append_item(
        content="Bob prefers Go",
        scope_type="chat",
        scope_id="milky:private:10002",
        kind="preference",
        title="lang",
    )

    # The chat-A dreamer is (wrongly) told to archive both items.
    provider = _FakeDreamProvider(
        json.dumps(
            {
                "add": [],
                "archive": [
                    {"item_id": item_a, "reason": "stale"},
                    {"item_id": item_b, "reason": "stale"},
                ],
            }
        )
    )

    consolidator = MemoryConsolidator(
        memory_store,
        default_scope_type="chat",
        default_scope_id="milky:private:10001",
    )
    await consolidator.consolidate_turn(
        session_id="milky:private:10001",
        user_message="unused",
        assistant_message="",
        dream_provider=provider,
        run_rules=False,
    )

    a_hits = await memory_store.search_items(
        "", scope_type="chat", scope_id="milky:private:10001", limit=10
    )
    b_hits = await memory_store.search_items(
        "", scope_type="chat", scope_id="milky:private:10002", limit=10
    )
    # item_a (in scope) is archived; item_b (other chat) survives.
    assert all(h.item_id != item_a for h in a_hits)
    assert any(h.item_id == item_b for h in b_hits)
