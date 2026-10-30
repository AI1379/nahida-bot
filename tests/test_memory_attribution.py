"""Behavioral regressions across authorship, scopes, tools and dreaming."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nahida_bot.agent.context import ContextBuilder
from nahida_bot.agent.memory.attribution import (
    MemoryAttribution,
    MemorySubject,
    item_subject,
)
from nahida_bot.agent.memory.dreaming import MemoryDreamingService
from nahida_bot.agent.memory.models import ConversationTurn, MemoryChanges
from nahida_bot.agent.memory.rendering import format_memory_items, memory_ref_metadata
from nahida_bot.agent.memory.service import MemoryService, project_workspace_memory
from nahida_bot.agent.memory.sqlite import SQLiteMemoryStore
from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.context import SessionContext
from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.plugins.builtin.tools.memory import MemoryTools


@pytest.fixture
async def memory(tmp_path):
    engine = DatabaseEngine(tmp_path / "memory.db")
    await engine.initialize()
    yield SQLiteMemoryStore(engine), engine
    await engine.close()


def context(sender="alice", chat="100"):
    return SessionContext(
        platform="milky",
        chat_id=chat,
        session_id=f"milky:group:{chat}",
        sender_account_key=f"milky:user:{sender}",
        person_id=f"person-{sender}",
        chat_address=ChatAddress.parse(f"milky:group:{chat}"),
    )


async def turn(store, sender, text, *, chat="100", role="user"):
    session = f"milky:group:{chat}"
    await store.ensure_session(session, workspace_id="default")
    return await store.append_turn(
        session,
        ConversationTurn(
            role,
            text,
            metadata={
                "message_context": {
                    "sender_id": sender,
                    "sender_display_name": sender.title(),
                    "message_id": f"msg-{sender}-{text[:5]}",
                    "chat_id": chat,
                },
            },
        ),
    )


def provider(add=(), archive=()):
    return SimpleNamespace(
        chat=AsyncMock(
            return_value=SimpleNamespace(
                content=json.dumps({"add": list(add), "archive": list(archive)})
            )
        )
    )


def claim(sender, ids, text="likes coffee"):
    return {
        "kind": "preference",
        "title": "Coffee",
        "content": text,
        "subject": {"kind": "account", "id": f"milky:user:{sender}"},
        "evidence_turn_ids": ids,
        "portable": True,
    }


async def test_two_people_keep_separate_subjects_and_tool_auto_views(memory):
    store, _ = memory
    alice = await turn(store, "alice", "I like coffee")
    bob = await turn(store, "bob", "I like coffee too")
    model = provider([claim("alice", [alice]), claim("bob", [bob])])
    outcome = await MemoryDreamingService(store).run_session(
        "milky:group:100", provider=model
    )
    assert outcome.applied == 2
    prompt = model.chat.call_args.kwargs["messages"][1].content
    assert "milky:user:alice" in prompt and "milky:user:bob" in prompt
    service = MemoryService(store, soft_scope=True)
    automatic = await service.retrieve(
        "coffee", ctx=context(chat="200"), automatic=True
    )
    explicit = await service.search_items_cascade("coffee", ctx=context(chat="200"))
    assert {r.result_id for r in automatic} == {i.item_id for i in explicit}
    assert {item_subject(i).id for i in explicit} == {
        "milky:user:alice",
        "milky:user:bob",
    }
    refs = [
        SimpleNamespace(
            key=i.item_id, content=i.content, metadata=memory_ref_metadata(i)
        )
        for i in explicit
    ]
    assert MemoryTools.format_refs(refs) == format_memory_items(explicit)
    rendered = MemoryTools.format_refs(refs)
    assert "milky:group:100" in rendered and f"Evidence turns: {alice}" in rendered


async def test_unknown_legacy_claims_require_explicit_recall_without_starving_known(
    memory,
):
    store, _ = memory
    for index in range(12):
        await store.append_item(
            content="coffee preference",
            title=f"legacy {index}",
            scope_type="chat",
            scope_id="milky:group:100",
            importance=1,
        )
    await store.append_item(
        content="coffee preference",
        scope_type="chat",
        scope_id="milky:group:100",
        importance=0,
        metadata={
            "attribution": MemoryAttribution(
                MemorySubject("account", "milky:user:alice")
            ).to_dict()
        },
    )
    service = MemoryService(store, soft_scope=True)
    auto = await service.retrieve(
        "coffee", ctx=context(chat="200"), limit=1, automatic=True
    )
    assert len(auto) == 1 and item_subject(auto[0].raw).id == "milky:user:alice"
    explicit = await service.search_items_cascade(
        "coffee", ctx=context(chat="200"), limit=20
    )
    assert any(item_subject(item).kind == "unknown" for item in explicit)
    assert "unknown" in format_memory_items(explicit)


async def test_shared_exports_never_enter_context_or_memory_tool(memory, tmp_path):
    store, _ = memory
    await store.append_item(
        content="Alice coffee",
        scope_type="chat",
        scope_id="milky:group:100",
        metadata={"portable": False},
    )
    await store.append_item(
        content="Bob coffee",
        scope_type="chat",
        scope_id="milky:group:200",
        metadata={"portable": False},
    )
    root = tmp_path / "shared"
    for chat in ("100", "200"):
        await project_workspace_memory(
            store, root, scope_type="chat", scope_id=f"milky:group:{chat}"
        )
        exported = (root / "MEMORY.md").read_text(encoding="utf-8")
        assert f"Export scope: chat:milky:group:{chat}" in exported
        assert "Subject:" in exported
        built = ContextBuilder().build_context(
            system_prompt="baseline", workspace_root=root
        )
        assert all("coffee" not in m.content for m in built)
    service = MemoryService(store, soft_scope=True)
    api = SimpleNamespace(
        memory_search=AsyncMock(return_value=[]),
        workspace_read=AsyncMock(return_value="Bob coffee"),
    )
    assert "Bob" not in await MemoryTools(api).read("coffee")
    api.workspace_read.assert_not_awaited()
    assert not await service.search_items_cascade("coffee", ctx=context(chat="300"))


async def test_third_party_evidence_never_becomes_current_speakers_fact(memory):
    store, _ = memory
    alice = await turn(store, "alice", "Bob said he likes coffee")
    bob = await turn(store, "bob", "Yes, coffee")
    service = MemoryService(store)
    item_id = await service.store_item(
        "Coffee",
        "Alice reports Bob likes coffee",
        ctx=context(),
        metadata={
            "subject_account_key": "milky:user:bob",
            "evidence_turn_ids": [alice, bob],
        },
    )
    item = (await store.get_items_by_ids([item_id]))[0]
    assert (item.scope_type, item.scope_id) == ("chat", "milky:group:100")
    assert item_subject(item).id == "milky:user:bob"
    unknown = await service.store_item("Unknown", "Someone likes tea", ctx=context())
    item = (await store.get_items_by_ids([unknown]))[0]
    assert item.scope_type == "chat" and item_subject(item).kind == "unknown"
    foreign = await turn(store, "bob", "private story", chat="200")
    with pytest.raises(ValueError, match="current chat"):
        await service.store_item(
            "Bad",
            "coffee",
            ctx=context(),
            metadata={
                "subject_account_key": "milky:user:bob",
                "evidence_turn_ids": [foreign],
            },
        )


@pytest.mark.parametrize("bad", ["missing", "outside", "assistant", "unknown"])
async def test_unsupported_candidates_are_reviewable_and_not_active(memory, bad):
    store, _ = memory
    user = await turn(store, "alice", "coffee")
    assistant = await turn(store, "", "I think Bob likes coffee", role="assistant")
    candidate = claim("alice", [user])
    if bad == "missing":
        candidate.pop("evidence_turn_ids")
    if bad == "outside":
        candidate["evidence_turn_ids"] = [99999]
    if bad == "assistant":
        candidate["evidence_turn_ids"] = [assistant]
    if bad == "unknown":
        candidate["subject"] = {"kind": "unknown", "id": ""}
    result = await MemoryDreamingService(store).run_session(
        "milky:group:100", provider=provider([candidate])
    )
    assert result.status == "applied" and result.applied == 0
    assert not await store.search_items("coffee", scope_type=None, scope_id=None)
    pending = await store.list_candidates(
        scope_type="chat", scope_id="milky:group:100", status="pending"
    )
    assert len(pending) == 1 and pending[0].metadata["rejection_reason"]
    assert pending[0].metadata["batch_turn_ids"] == [user, assistant]


async def test_failed_dream_retries_same_evidence_without_cursor_advance(memory):
    store, _ = memory
    first = await turn(store, "alice", "coffee")
    failing = SimpleNamespace(
        chat=AsyncMock(side_effect=RuntimeError("provider failed"))
    )
    service = MemoryDreamingService(store)
    failed = await service.run_session("milky:group:100", provider=failing)
    assert failed.status == "failed"
    assert not (await store.get_session_meta("milky:group:100")).get(
        "memory_dream_last_turn_id"
    )
    success = await service.run_session(
        "milky:group:100", provider=provider([claim("alice", [first])])
    )
    assert success.applied == 1
    again = await service.run_session("milky:group:100", provider=failing)
    assert again.status == "empty" and failing.chat.await_count == 1


async def test_batches_consume_oldest_complete_messages_and_keep_final_correction(
    memory,
):
    store, _ = memory
    ids = [await turn(store, "alice", "long context " * 55) for _ in range(5)]
    correction = await turn(store, "bob", "CORRECTION_AT_END: that was Alice, not me")
    model = provider()
    dream = MemoryDreamingService(store)
    processed = []
    for _ in range(10):
        result = await dream.run_session(
            "milky:group:100", provider=model, turn_limit=2, max_chars=1400
        )
        if result.status == "empty":
            break
        assert result.status == "applied"
        processed.append(result.last_turn_id)
    assert processed[0] == ids[0]
    assert processed[-1] == correction
    assert processed == sorted(set(processed))
    prompts = [c.kwargs["messages"][1].content for c in model.chat.call_args_list]
    assert "CORRECTION_AT_END" in prompts[-1]
    all_ids = [
        record["turn_id"]
        for call in model.chat.call_args_list
        for line in call.kwargs["messages"][1].content.splitlines()
        if (raw := line.removeprefix("User: ")).startswith('{"turn_id"')
        for record in [json.loads(raw)]
    ]
    assert all_ids == [*ids, correction]


async def test_oversized_single_record_is_visible_failure_without_skipping(memory):
    store, _ = memory
    await turn(store, "alice", "oversize " * 1000)
    model = provider()
    result = await MemoryDreamingService(store).run_session(
        "milky:group:100", provider=model, max_chars=500
    )
    assert result.status == "failed" and "review required" in result.error
    model.chat.assert_not_awaited()
    assert not (await store.get_session_meta("milky:group:100")).get(
        "memory_dream_last_turn_id"
    )


async def test_atomic_commit_rolls_back_items_and_cursor_and_rejects_stale_retry(
    memory, monkeypatch
):
    store, engine = memory
    last = await turn(store, "alice", "coffee")
    service = MemoryService(store)
    item = service.prepare_item(
        "Coffee",
        "Alice likes coffee",
        ctx=context(),
        metadata={"subject": "current_sender"},
    )
    changes = MemoryChanges(additions=[item])
    execute = engine.execute

    async def fail(sql, params=None):
        if "UPDATE sessions SET metadata_json" in sql:
            raise RuntimeError("commit interrupted")
        return await execute(sql, params)

    monkeypatch.setattr(engine, "execute", fail)
    with pytest.raises(RuntimeError):
        await store.apply_memory_changes(
            changes, session_id="milky:group:100", expected_turn_id=0, last_turn_id=last
        )
    assert not await store.get_items_by_ids([item.item_id])
    monkeypatch.setattr(engine, "execute", execute)
    assert (
        await store.apply_memory_changes(
            changes, session_id="milky:group:100", expected_turn_id=0, last_turn_id=last
        )
        == 1
    )
    assert (
        await store.apply_memory_changes(
            changes, session_id="milky:group:100", expected_turn_id=0, last_turn_id=last
        )
        == -1
    )


async def test_update_preserves_original_subject_in_new_speaker_context(memory):
    store, _ = memory
    service = MemoryService(store)
    original = service.prepare_item(
        "Coffee",
        "Alice likes coffee",
        ctx=context(),
        metadata={"subject": "current_sender", "portable": False},
    )
    await service.apply_changes(MemoryChanges(additions=[original]))
    replacement_id = await service.update_item_for_context(
        original.item_id,
        "Alice prefers tea now",
        ctx=context(sender="bob"),
        metadata={"audience": "current"},
    )
    replacement = (await store.get_items_by_ids([replacement_id]))[0]
    assert item_subject(replacement).id == "milky:user:alice"
    assert not await store.get_items_by_ids([original.item_id])


async def test_literal_search_keeps_scope_and_sensitivity(memory):
    store, _ = memory
    await store.append_item(
        content="prefoobarsuffix", scope_type="chat", scope_id="milky:group:100"
    )
    await store.append_item(
        content="privatefoobar",
        scope_type="chat",
        scope_id="milky:group:200",
        sensitivity="private",
    )
    results = await MemoryService(store, soft_scope=True).search_items_cascade(
        "ooba", ctx=context()
    )
    assert len(results) == 1 and results[0].content == "prefoobarsuffix"


async def test_group_facts_remain_available_without_cross_scope_recall(memory):
    store, _ = memory
    first = await turn(store, "alice", "coffee")
    await MemoryDreamingService(store).run_session(
        "milky:group:100", provider=provider([claim("alice", [first])])
    )
    results = await MemoryService(store, soft_scope=False).retrieve(
        "coffee", ctx=context(), automatic=True
    )
    assert len(results) == 1
    assert item_subject(results[0].raw).id == "milky:user:alice"


async def test_rule_consolidation_preserves_the_original_message_evidence(memory):
    from nahida_bot.agent.memory.consolidation import MemoryConsolidator

    store, _ = memory
    first = await turn(store, "alice", "请记住：我喜欢咖啡，不要替我换成茶。")
    records = await store.get_turns_by_ids([first])
    await MemoryConsolidator(
        store, default_scope_type="chat", default_scope_id="milky:group:100"
    ).consolidate_turn(
        session_id="milky:group:100",
        user_message=records[0].turn.content,
        records=records,
        sender_account_key="milky:user:alice",
    )
    items = await store.search_items(
        "咖啡", scope_type="chat", scope_id="milky:group:100"
    )
    assert len(items) == 1
    attribution = MemoryAttribution.parse(items[0].metadata)
    assert attribution.subject.id == "milky:user:alice"
    assert attribution.evidence[0].turn_id == first


async def test_malformed_dream_is_failure_not_successful_empty_batch(memory):
    store, _ = memory
    await turn(store, "alice", "coffee")
    model = SimpleNamespace(
        chat=AsyncMock(
            return_value=SimpleNamespace(content='{"add": null, "archive": []}')
        )
    )
    outcome = await MemoryDreamingService(store).run_session(
        "milky:group:100", provider=model
    )
    assert outcome.status == "failed"
    assert not (await store.get_session_meta("milky:group:100")).get(
        "memory_dream_last_turn_id"
    )


async def test_batch_alias_is_tied_to_the_self_identifying_account(memory):
    store, _ = memory
    first = await turn(store, "alice", "这个群里大家都叫我茶茶。")
    await turn(store, "bob", "我喜欢咖啡")
    alias = claim("alice", [first], "Alice is called 茶茶 in this group")
    alias.update(relation="alias", alias="茶茶", kind="fact")
    outcome = await MemoryDreamingService(store).run_session(
        "milky:group:100", provider=provider([alias])
    )
    assert outcome.applied == 1
    item = (
        await store.search_items("茶茶", scope_type="chat", scope_id="milky:group:100")
    )[0]
    assert item_subject(item).id == "milky:user:alice"
    assert item.metadata["portable"] is False
    assert not await MemoryService(store, soft_scope=True).search_items_cascade(
        "茶茶", ctx=context(chat="200")
    )


async def test_attribution_audit_does_not_modify_the_database(memory, tmp_path):
    from scripts.audit_memory_attribution import audit_database

    store, engine = memory
    unknown = await store.append_item(content="a legacy preference", kind="preference")
    before = await engine.fetch_one(
        "SELECT metadata_json FROM memory_items WHERE item_id=?", (unknown,)
    )
    report = audit_database(tmp_path / "memory.db")
    assert report["read_only"] is True
    assert report["problem_counts"]["unknown_subject"] == 1
    assert report["findings"][0]["item_id"] == unknown
    after = await engine.fetch_one(
        "SELECT metadata_json FROM memory_items WHERE item_id=?", (unknown,)
    )
    assert before["metadata_json"] == after["metadata_json"]


@pytest.mark.parametrize(
    "text,accepted", [("I like coffee", False), ("Bob likes coffee", True)]
)
async def test_reported_subject_must_be_named_in_the_cited_evidence(
    memory, text, accepted
):
    store, _ = memory
    alice = await turn(store, "alice", text)
    await turn(store, "bob", "hello")
    outcome = await MemoryDreamingService(store).run_session(
        "milky:group:100", provider=provider([claim("bob", [alice])])
    )
    assert outcome.applied == int(accepted)
    if accepted:
        items = await store.search_items(
            "coffee", scope_type="chat", scope_id="milky:group:100"
        )
        rendered = format_memory_items(items)
        assert "Subject: account:milky:user:bob" in rendered
        assert "Reported by: milky:user:alice" in rendered


async def test_explicit_cross_chat_tool_keeps_raw_speakers_and_memory_subjects(
    memory, tmp_path
):
    from tests.test_api_bridge import _api
    from nahida_bot.plugins.builtin.tools.history import HistoryTools

    store, _ = memory
    alice = await turn(store, "alice", "I like coffee")
    await MemoryDreamingService(store).run_session(
        "milky:group:100", provider=provider([claim("alice", [alice])])
    )
    api, _, _, _ = _api(tmp_path, memory_store=store)
    rows = await api.recall_cross_chat("coffee")
    raw = next(row for row in rows if row["source_type"] == "conversation_turns")
    assert raw["message_context"]["sender_id"] == "alice"
    structured = next(row for row in rows if row["source_type"] == "memory")
    assert structured["attribution"]["subject"]["id"] == "milky:user:alice"
    output = await HistoryTools(api).recall("coffee")
    assert "Sender: alice" in output
    assert "Subject: account:milky:user:alice" in output
