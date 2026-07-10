"""Tests for cross-session chat history search + chat name lookup.

Covers:
- ``SQLiteChatMetadataRepository`` (observe upsert / search / get_many)
- router chat-name capture in ``_build_session_context``
- ``SQLiteMemoryStore.search_turns`` cross-session reuse target
- ``_sanitize_turn_for_model`` base64 stripping + truncation
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from nahida_bot.agent.memory.sqlite import SQLiteMemoryStore
from nahida_bot.agent.memory.models import ConversationTurn
from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.message_context import chat_context_from_values
from nahida_bot.core.router import MessageRouter
from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.db.repositories.sqlite_chat_metadata_repo import (
    SQLiteChatMetadataRepository,
)
from nahida_bot.plugins.base import InboundMessage, SenderContext


def _message_metadata(
    message_id: str,
    *,
    sender: str,
    reply_to: str = "",
    observed: bool = True,
) -> dict[str, object]:
    return {
        "message_id": message_id,
        "reply_to": reply_to,
        "observed_only": observed,
        "trigger_kind": "observed" if observed else "mention",
        "message_context": {
            "channel": "milky",
            "chat_type": "group",
            "chat_id": "1",
            "sender_id": sender,
            "sender_display_name": sender.title(),
            "message_id": message_id,
            "reply_to_message_id": reply_to,
        },
    }


@pytest.fixture
async def engine() -> DatabaseEngine:
    eng = DatabaseEngine(":memory:")
    await eng.initialize()
    yield eng
    await eng.close()


# ── ChatMetadataRepository ───────────────────────────────────────


@pytest.mark.asyncio
async def test_observe_then_search(engine: DatabaseEngine) -> None:
    store = SQLiteChatMetadataRepository(engine)
    await store.observe(
        "milky:group:20001",
        platform="milky",
        target_type="group",
        target_id="20001",
        display_name="原神交流群",
    )
    rows = await store.search_by_name("原神")
    assert len(rows) == 1
    assert rows[0]["chat_address"] == "milky:group:20001"
    assert rows[0]["display_name"] == "原神交流群"


@pytest.mark.asyncio
async def test_search_case_insensitive_substring(engine: DatabaseEngine) -> None:
    store = SQLiteChatMetadataRepository(engine)
    await store.observe(
        "milky:group:1",
        platform="milky",
        target_type="group",
        target_id="1",
        display_name="Genshin Friends",
    )
    rows = await store.search_by_name("genshin")  # ASCII LIKE is case-insensitive
    assert len(rows) == 1
    assert rows[0]["display_name"] == "Genshin Friends"


@pytest.mark.asyncio
async def test_search_platform_filter(engine: DatabaseEngine) -> None:
    store = SQLiteChatMetadataRepository(engine)
    await store.observe(
        "milky:group:1",
        platform="milky",
        target_type="group",
        target_id="1",
        display_name="原神群",
    )
    await store.observe(
        "telegram:group:2",
        platform="telegram",
        target_type="group",
        target_id="2",
        display_name="原神群",
    )
    rows = await store.search_by_name("原神", platform="milky")
    assert len(rows) == 1
    assert rows[0]["platform"] == "milky"


@pytest.mark.asyncio
async def test_observe_upsert_keeps_first_seen(engine: DatabaseEngine) -> None:
    store = SQLiteChatMetadataRepository(engine)
    await store.observe(
        "milky:group:1",
        platform="milky",
        target_type="group",
        target_id="1",
        display_name="Old Name",
    )
    first = await store.get("milky:group:1")
    assert first is not None
    await store.observe(
        "milky:group:1",
        platform="milky",
        target_type="group",
        target_id="1",
        display_name="New Name",
    )
    second = await store.get("milky:group:1")
    assert second is not None
    assert second["display_name"] == "New Name"
    # first_seen preserved, last_seen refreshed.
    assert second["first_seen_at"] == first["first_seen_at"]
    assert second["last_seen_at"] >= first["last_seen_at"]


@pytest.mark.asyncio
async def test_observe_empty_name_is_noop(engine: DatabaseEngine) -> None:
    store = SQLiteChatMetadataRepository(engine)
    await store.observe(
        "milky:group:1",
        platform="milky",
        target_type="group",
        target_id="1",
        display_name="",
    )
    assert await store.get("milky:group:1") is None


@pytest.mark.asyncio
async def test_get_many(engine: DatabaseEngine) -> None:
    store = SQLiteChatMetadataRepository(engine)
    await store.observe(
        "milky:group:1",
        platform="milky",
        target_type="group",
        target_id="1",
        display_name="A",
    )
    await store.observe(
        "milky:group:2",
        platform="milky",
        target_type="group",
        target_id="2",
        display_name="B",
    )
    name_map = await store.get_many(["milky:group:1", "milky:group:3", "milky:group:2"])
    assert name_map == {"milky:group:1": "A", "milky:group:2": "B"}


@pytest.mark.asyncio
async def test_get_many_empty(engine: DatabaseEngine) -> None:
    store = SQLiteChatMetadataRepository(engine)
    assert await store.get_many([]) == {}
    assert await store.get_many(["milky:group:99"]) == {}


# ── Router chat-name capture ─────────────────────────────────────


def _inbound_with_chat_name(name: str) -> InboundMessage:
    return InboundMessage(
        message_id="m1",
        platform="milky",
        chat_id="20001",
        user_id="u1",
        text="hi",
        raw_event={},
        sender_context=SenderContext(platform_user_id="10001"),
        chat_context=chat_context_from_values(
            platform="milky",
            chat_type="group",
            platform_chat_id="20001",
            display_name=name,
        ),
    )


def _router(chat_store: SQLiteChatMetadataRepository | None) -> MessageRouter:
    return MessageRouter(
        event_bus=MagicMock(),
        command_registry=MagicMock(),
        command_matcher=MagicMock(),
        channel_registry=MagicMock(),
        runner=None,
        workspace_manager=None,
        config=None,
        identity_resolver=None,
        chat_metadata_store=chat_store,
    )


@pytest.mark.asyncio
async def test_router_captures_chat_name(engine: DatabaseEngine) -> None:
    store = SQLiteChatMetadataRepository(engine)
    router = _router(store)
    address = ChatAddress(channel="milky", target_type="group", target_id="20001")
    await router._build_session_context(
        _inbound_with_chat_name("原神交流群"),
        address,
        "milky:group:20001",
        None,
    )
    row = await store.get("milky:group:20001")
    assert row is not None
    assert row["display_name"] == "原神交流群"
    assert row["platform"] == "milky"


@pytest.mark.asyncio
async def test_router_skips_empty_name(engine: DatabaseEngine) -> None:
    store = SQLiteChatMetadataRepository(engine)
    router = _router(store)
    address = ChatAddress(channel="milky", target_type="group", target_id="20001")
    await router._build_session_context(
        _inbound_with_chat_name(""),
        address,
        "milky:group:20001",
        None,
    )
    assert await store.get("milky:group:20001") is None


@pytest.mark.asyncio
async def test_router_no_store_is_noop() -> None:
    # chat_metadata_store=None must not raise and still returns a context.
    router = _router(None)
    address = ChatAddress(channel="milky", target_type="group", target_id="20001")
    ctx = await router._build_session_context(
        _inbound_with_chat_name("X"),
        address,
        "milky:group:20001",
        None,
    )
    assert ctx is not None


# ── search_turns cross-session reuse target ──────────────────────


@pytest.mark.asyncio
async def test_search_turns_cross_session(engine: DatabaseEngine) -> None:
    store = SQLiteMemoryStore(engine)
    await store.ensure_session("milky:group:1")
    await store.ensure_session("milky:group:2")
    await store.append_turn(
        "milky:group:1",
        ConversationTurn(role="user", content="we talked about dragons in group A"),
    )
    await store.append_turn(
        "milky:group:2",
        ConversationTurn(role="assistant", content="dragons are cool in group B"),
    )
    rows = await store.search_turns("dragons")
    assert len(rows) == 2
    assert {r.session_id for r in rows} == {"milky:group:1", "milky:group:2"}


@pytest.mark.asyncio
async def test_search_turns_chat_address_prefix(engine: DatabaseEngine) -> None:
    store = SQLiteMemoryStore(engine)
    await store.ensure_session("milky:group:1")
    await store.ensure_session("milky:group:2")
    await store.append_turn(
        "milky:group:1", ConversationTurn(role="user", content="dragons here")
    )
    await store.append_turn(
        "milky:group:2", ConversationTurn(role="user", content="dragons there")
    )
    rows = await store.search_turns("dragons", chat_address="milky:group:1")
    assert len(rows) == 1
    assert rows[0].session_id == "milky:group:1"


@pytest.mark.asyncio
async def test_read_chat_turns_spans_derived_sessions_and_supports_cursor(
    engine: DatabaseEngine,
) -> None:
    store = SQLiteMemoryStore(engine)
    now = datetime.now(UTC)
    inserted_after = now - timedelta(seconds=1)
    for session_id, content, message_id, offset in (
        ("milky:group:1", "first", "m1", 1),
        ("milky:group:1:topic", "second", "m2", 2),
        ("milky:group:2", "other chat", "x1", 3),
        ("milky:group:1:topic", "third", "m3", 4),
    ):
        await store.ensure_session(session_id)
        await store.append_turn(
            session_id,
            ConversationTurn(
                role="user",
                content=content,
                metadata=_message_metadata(message_id, sender="alice"),
                created_at=now + timedelta(seconds=offset),
            ),
        )

    rows = await store.read_chat_turns(chat_address="milky:group:1", limit=10)
    assert [row.turn.content for row in rows] == ["first", "second", "third"]

    cursor_rows = await store.read_chat_turns(
        chat_address="milky:group:1",
        before_turn_id=rows[-1].turn_id,
        limit=10,
    )
    assert [row.turn.content for row in cursor_rows] == ["first", "second"]

    ranged = await store.read_chat_turns(
        chat_address="milky:group:1",
        since=inserted_after,
        until=datetime.now(UTC) + timedelta(seconds=1),
        limit=10,
    )
    assert [row.turn.content for row in ranged] == ["first", "second", "third"]


@pytest.mark.asyncio
async def test_find_message_and_read_neighbors(engine: DatabaseEngine) -> None:
    store = SQLiteMemoryStore(engine)
    await store.ensure_session("milky:group:1")
    for index in range(1, 6):
        await store.append_turn(
            "milky:group:1",
            ConversationTurn(
                role="user",
                content=f"message {index}",
                metadata=_message_metadata(
                    f"m{index}",
                    sender=f"u{index}",
                    reply_to="m2" if index == 4 else "",
                ),
            ),
        )

    anchor = await store.find_turn_by_message_id("m3", chat_address="milky:group:1")
    assert anchor is not None
    assert anchor.turn.content == "message 3"

    around = await store.read_turns_around(
        anchor.turn_id,
        chat_address="milky:group:1",
        before=1,
        after=2,
    )
    assert [row.turn.content for row in around] == [
        "message 2",
        "message 3",
        "message 4",
        "message 5",
    ]


# ── Sanitization ─────────────────────────────────────────────────


def test_sanitize_strips_base64_data_url() -> None:
    from nahida_bot.plugins.builtin.commands import BuiltinCommandsPlugin

    data_url = "data:image/png;base64," + "A" * 500
    sanitized = BuiltinCommandsPlugin._sanitize_turn_for_model(
        f"look {data_url} and text"
    )
    assert "base64" not in sanitized
    assert "[media omitted]" in sanitized
    assert "and text" in sanitized


def test_sanitize_strips_long_base64_blob() -> None:
    from nahida_bot.plugins.builtin.commands import BuiltinCommandsPlugin

    blob = "Q" * 400  # not a data URL, just a long base64-ish run
    sanitized = BuiltinCommandsPlugin._sanitize_turn_for_model(f"pre {blob} post")
    assert "[data omitted]" in sanitized
    assert "pre" in sanitized
    assert "post" in sanitized


def test_sanitize_truncates_long_content() -> None:
    from nahida_bot.plugins.builtin.commands import BuiltinCommandsPlugin

    # Realistic long text (spaces break up base64 runs) should truncate, not be
    # fully elided.
    sanitized = BuiltinCommandsPlugin._sanitize_turn_for_model("word " * 1000)
    assert len(sanitized) <= 1500 + len("...")
    assert sanitized.endswith("...")
