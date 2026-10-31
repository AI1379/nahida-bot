"""Behavioral contracts for structured memory and confirmed cross-channel identity."""

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from nahida_bot.agent.memory.attribution import MemoryAttribution, MemorySubject
from nahida_bot.agent.memory.models import MemoryChanges
from nahida_bot.agent.memory.rendering import memory_ref_metadata
from nahida_bot.agent.memory.service import MemoryService
from nahida_bot.agent.memory.sqlite import SQLiteMemoryStore
from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.context import SessionContext
from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.identity.management import IdentityManager
from nahida_bot.identity.sqlite import SQLiteIdentityStore
from nahida_bot.plugins.conversation_joiner.config import InnerThoughtsConfig
from nahida_bot.plugins.conversation_joiner.social_memory import appraisal_memories
from nahida_bot.plugins.conversation_joiner.social_state import (
    Evidence,
    SocialState,
    SocialUpdate,
)
from nahida_bot_sdk.plugin import MemoryRef


@pytest.fixture
async def stores(tmp_path):
    engine = DatabaseEngine(tmp_path / "memory.db")
    await engine.initialize()
    memory = SQLiteMemoryStore(engine)
    identity = SQLiteIdentityStore(engine)
    yield memory, identity, engine
    await engine.close()


def context(account="milky:user:alice", *, person=None, chat=""):
    channel, _, user = account.split(":")
    address = ChatAddress.parse(chat or f"{channel}:private:{user}")
    return SessionContext(
        platform=channel,
        chat_id=address.target_id,
        session_id=address.chat_key,
        chat_address=address,
        sender_account_key=account,
        person_id=person,
    )


async def link_pair(identity, a="milky:user:alice", b="telegram:user:alice"):
    proposal = await identity.mutations.propose(
        a, b, {"statement": "That is also my account"}
    )
    return await identity.mutations.resolve(proposal["candidate_id"], b, confirm=True)


async def test_proposal_cannot_resolve_identity_or_read_private_memory(stores):
    memory, identity, _ = stores
    service = MemoryService(memory, soft_scope=True)
    await service.store_item(
        "coffee",
        "coffee private preference",
        ctx=context(),
        metadata={"subject": "current_sender", "sensitivity": "private"},
    )
    candidate = await identity.mutations.propose(
        "milky:user:alice", "telegram:user:alice", {}
    )
    assert await identity.resolve_account("telegram:user:alice") == (None, "none")
    assert not await service.search_items_cascade(
        "coffee", ctx=context("telegram:user:alice")
    )
    assert candidate["status"] == "pending"
    assert not await identity.mutations.candidates("discord:user:outsider")


@pytest.mark.parametrize("actor", ["milky:user:alice", "discord:user:outsider"])
async def test_only_counterparty_can_confirm(stores, actor):
    _, identity, _ = stores
    candidate = await identity.mutations.propose(
        "milky:user:alice", "telegram:user:alice", {}
    )
    with pytest.raises(ValueError):
        await identity.mutations.resolve(candidate["candidate_id"], actor, confirm=True)
    assert await identity.resolve_account("milky:user:alice") == (None, "none")


async def test_confirmation_shares_old_private_memory_but_not_secret_or_chat_bound(
    stores,
):
    memory, identity, _ = stores
    service = MemoryService(memory, soft_scope=True)
    for n, (sensitivity, portable) in enumerate(
        [("private", True), ("secret_like", True), ("private", False)]
    ):
        await service.store_item(
            "coffee",
            f"coffee note {n}",
            ctx=context(),
            metadata={
                "subject": "current_sender",
                "sensitivity": sensitivity,
                "portable": portable,
            },
        )
    result = await link_pair(identity)
    other = context("telegram:user:alice", person=result["person_id"])
    items = await service.search_items_cascade("coffee", ctx=other)
    assert [i.content for i in items] == ["coffee note 0"]
    assert items[0].metadata["present_subject_accounts"] == {
        "account:milky:user:alice": ["telegram:user:alice"]
    }
    assert (
        "present_subject_accounts"
        not in (await memory.get_items_by_ids([items[0].item_id]))[0].metadata
    )
    dossier = await service.context_items(ctx=other)
    assert [i.content for i in dossier["person"]] == ["coffee note 0"]
    await identity.mutations.unlink("telegram:user:alice", "telegram:user:alice")
    # Even an already-built context cannot retain a revoked mapping.
    assert not await service.search_items_cascade("coffee", ctx=other)


async def test_group_never_inherits_confirmed_person_private_memory(stores):
    memory, identity, _ = stores
    service = MemoryService(memory, soft_scope=True)
    result = await link_pair(identity)
    await service.store_item(
        "coffee",
        "coffee private person fact",
        ctx=context(person=result["person_id"]),
        metadata={"subject": "current_sender", "sensitivity": "private"},
    )
    group = context(
        "telegram:user:alice", person=result["person_id"], chat="telegram:group:team"
    )
    assert not await service.search_items_cascade("coffee", ctx=group)
    assert not (await service.context_items(ctx=group))["person"]


async def test_same_channel_rejected_by_claim_admin_and_storage(stores):
    _, identity, engine = stores
    with pytest.raises(ValueError, match="different channels"):
        await identity.mutations.propose("milky:user:alice", "milky:user:alt", {})
    manager = IdentityManager(identity)
    await manager.create_or_update_person(person_id="alice", actor="operator")
    await manager.link_account(
        account_key="milky:user:alice", person_id="alice", actor="operator"
    )
    with pytest.raises(ValueError, match="different channels"):
        await manager.link_account(
            account_key="milky:user:alt", person_id="alice", actor="operator"
        )
    with pytest.raises(Exception, match="different channels"):
        await engine.execute(
            "INSERT INTO person_accounts SELECT 'milky:user:alt', person_id, channel, account_type, 'alt', label, status, verification, linked_by, linked_at, metadata_json FROM person_accounts WHERE account_key='milky:user:alice'"
        )
    await engine.db.rollback()


async def test_binding_and_audit_roll_back_together(stores, monkeypatch):
    _, identity, _ = stores
    manager = IdentityManager(identity)
    await manager.create_or_update_person(person_id="alice", actor="operator")
    monkeypatch.setattr(
        identity.mutations, "audit", AsyncMock(side_effect=RuntimeError("audit failed"))
    )
    with pytest.raises(RuntimeError):
        await manager.link_account(
            account_key="milky:user:alice", person_id="alice", actor="operator"
        )
    assert await identity.resolve_account("milky:user:alice") == (None, "none")


async def test_failed_confirmation_cannot_leave_half_person_or_links(
    stores, monkeypatch
):
    _, identity, engine = stores
    candidate = await identity.mutations.propose(
        "milky:user:alice", "telegram:user:alice", {}
    )
    monkeypatch.setattr(
        identity.mutations, "audit", AsyncMock(side_effect=RuntimeError("audit failed"))
    )
    with pytest.raises(RuntimeError):
        await identity.mutations.resolve(
            candidate["candidate_id"], "telegram:user:alice", confirm=True
        )
    assert not await identity.list_people()
    row = await engine.fetch_one(
        "SELECT status FROM identity_link_candidates WHERE candidate_id=?",
        (candidate["candidate_id"],),
    )
    assert row["status"] == "pending"


async def test_changed_identity_invalidates_candidate(stores):
    _, identity, _ = stores
    candidate = await identity.mutations.propose(
        "milky:user:alice", "telegram:user:alice", {}
    )
    manager = IdentityManager(identity)
    await manager.create_or_update_person(person_id="changed", actor="operator")
    await manager.link_account(
        account_key="milky:user:alice", person_id="changed", actor="operator"
    )
    with pytest.raises(ValueError, match="changed"):
        await identity.mutations.resolve(
            candidate["candidate_id"], "telegram:user:alice", confirm=True
        )


async def test_candidate_idempotency_rejection_and_persistence(stores):
    _, identity, engine = stores
    first = await identity.mutations.propose(
        "milky:user:alice", "telegram:user:alice", {}
    )
    second = await identity.mutations.propose(
        "telegram:user:alice", "milky:user:alice", {}
    )
    assert first == second
    other_store = SQLiteIdentityStore(engine)
    assert len(await other_store.mutations.candidates("milky:user:alice")) == 1
    await other_store.mutations.resolve(
        first["candidate_id"], "milky:user:alice", confirm=False
    )
    assert not await identity.mutations.candidates("telegram:user:alice")
    with pytest.raises(ValueError, match="resolved"):
        await identity.mutations.resolve(
            first["candidate_id"], "telegram:user:alice", confirm=True
        )


async def test_dossier_has_distinct_categories_and_filters_before_limit(stores):
    memory, _, _ = stores
    service = MemoryService(memory, soft_scope=True)
    ctx = context(chat="milky:group:room")
    subjects = [
        MemorySubject("bot", "self"),
        MemorySubject("environment", "runtime"),
        MemorySubject("account", "milky:user:alice"),
        MemorySubject("chat", "milky:group:room"),
        MemorySubject.parse(
            {
                "kind": "relationship",
                "members": [
                    {"kind": "account", "id": "milky:user:alice"},
                    {"kind": "account", "id": "milky:user:bob"},
                ],
            }
        ),
    ]
    for subject in subjects:
        item = service.prepare_item(
            subject.category,
            "remembered " + subject.category,
            ctx=ctx,
            metadata={
                "kind": "impression",
                "attribution": MemoryAttribution(subject).to_dict(),
            },
        )
        await service.apply_changes(MemoryChanges(additions=[item]))
    for i in range(8):
        await memory.append_item(
            content="irrelevant",
            scope_type="chat",
            scope_id="milky:group:room",
            importance=1,
            metadata={
                "attribution": MemoryAttribution(
                    MemorySubject("account", f"milky:user:stranger{i}")
                ).to_dict()
            },
        )
    sections = await service.context_items(ctx=ctx, limit_per_category=1)
    assert set(sections) == {"bot", "environment", "person", "chat", "relationship"}
    assert all(len(items) == 1 for items in sections.values())
    assert sections["person"][0].content == "remembered person"


def test_relationship_members_are_canonical_and_not_names():
    a = {"kind": "account", "id": "milky:user:a"}
    b = {"kind": "account", "id": "milky:user:b"}
    assert MemorySubject.parse(
        {"kind": "relationship", "members": [a, b]}
    ) == MemorySubject.parse({"kind": "relationship", "members": [b, a]})
    assert (
        MemorySubject.parse({"kind": "relationship", "members": [a, a]}).kind
        == "unknown"
    )
    assert (
        MemorySubject.parse(
            {"kind": "relationship", "members": [a, {"kind": "account", "id": "Alice"}]}
        ).kind
        == "unknown"
    )


async def test_relationship_writes_validate_each_participants_evidence(stores):
    from nahida_bot.agent.memory.models import ConversationTurn

    memory, _, _ = stores
    ctx = context(chat="milky:group:room")
    await memory.ensure_session(ctx.session_id)
    ids = []
    for sender, text in [
        ("alice", "Bob and I are friends"),
        ("bob", "Alice is my friend"),
    ]:
        ids.append(
            await memory.append_turn(
                ctx.session_id,
                ConversationTurn(
                    "user",
                    text,
                    metadata={
                        "message_context": {
                            "sender_id": sender,
                            "sender_display_name": sender.title(),
                        }
                    },
                ),
            )
        )
    service = MemoryService(memory)
    values = {
        "subject": "relationship",
        "relationship_accounts": ["milky:user:alice", "milky:user:bob"],
        "evidence_turn_ids": ids,
    }
    item_id = await service.store_item(
        "friendship",
        "Alice and Bob describe each other as friends",
        ctx=ctx,
        metadata=values,
    )
    item = (await memory.get_items_by_ids([item_id]))[0]
    assert MemoryAttribution.parse(item.metadata).subject.kind == "relationship"
    with pytest.raises(ValueError):
        await service.store_item(
            "wrong",
            "Wrong relation",
            ctx=ctx,
            metadata={
                **values,
                "relationship_accounts": ["milky:user:alice", "milky:user:charlie"],
            },
        )


async def test_memories_can_inform_feelings_but_cannot_replace_message_evidence(stores):
    memory, _, _ = stores
    service = MemoryService(memory)
    item = service.prepare_item(
        "friendship",
        "Alice and Bob enjoy playing together",
        ctx=context(),
        metadata={"subject": "current_sender"},
    )
    refs = {
        "person": [
            MemoryRef(
                key=item.item_id,
                content=item.content,
                metadata=memory_ref_metadata(item),
            )
        ]
    }
    basis = appraisal_memories(refs, max_items=6, max_chars=2400)
    state = SocialState(
        session_id="test",
        chat_key="milky:group:room",
        persona_hash="p",
        memory_basis=basis,
        evidence=[
            Evidence(
                message_id="m1", sender_id="alice", text="Bob is back", observed_at=100
            )
        ],
    )
    update = SocialUpdate.model_validate(
        {
            "feeling": {
                "text": "Glad they can play together again",
                "label": "happy",
                "intensity": 0.4,
                "subject_id": "self",
                "evidence_ids": ["m1"],
                "memory_ids": [item.item_id],
            },
            "thoughts": [],
        }
    )
    state.apply(update, 100, InnerThoughtsConfig())
    assert state.feeling.memory_ids == [item.item_id]
    invalid = update.feeling.model_copy(update={"evidence_ids": ["invented"]})
    assert not state.grounded(invalid)
    invalid = update.feeling.model_copy(update={"memory_ids": ["hidden-private"]})
    assert not state.grounded(invalid)
    state.memory_basis = []
    state.prune(101, InnerThoughtsConfig())
    assert state.feeling is None


async def test_api_requires_literal_counterparty_confirmation(stores, tmp_path):
    from tests.test_api_bridge import _api
    from nahida_bot.core.context import current_session
    from nahida_bot_sdk.messaging import InboundMessage
    from nahida_bot.identity.resolver import IdentityResolver
    from nahida_bot.identity.authorization import AuthorizationGate

    memory, identity, _ = stores
    api, _, _, _ = _api(tmp_path, memory_store=memory)
    app = api._event_bus.context.app
    app._identity_store = identity
    app._identity_resolver = IdentityResolver(identity, enabled=True)

    def inbound_ctx(account, text):
        ctx = context(account)
        return replace(
            ctx,
            inbound_message=InboundMessage(
                message_id="m1",
                platform=ctx.platform,
                chat_id=ctx.chat_id,
                user_id=account.rsplit(":", 1)[1],
                text=text,
                raw_event={},
            ),
        )

    token = current_session.set(
        inbound_ctx("milky:user:alice", "My Telegram account is also mine")
    )
    try:
        candidate = await api.identity_claim(
            "propose", account_key="telegram:user:alice"
        )
        key = candidate["candidate_id"]
        current_session.set(
            inbound_ctx("telegram:user:alice", "The model thinks this is confirmed")
        )
        with pytest.raises(ValueError, match="exact"):
            await api.identity_claim("confirm", candidate_id=key)
        current_session.set(
            inbound_ctx("telegram:user:alice", f"/identity confirm {key}")
        )
        assert (await api.identity_claim("confirm", candidate_id=key))[
            "status"
        ] == "confirmed"
        assert not AuthorizationGate(frozenset({"milky:user:alice"})).is_admin(
            "telegram:user:alice"
        )
        current_session.set(inbound_ctx("telegram:user:alice", "/identity revoke"))
        assert (await api.identity_claim("revoke"))["unlinked"]
    finally:
        current_session.reset(token)


async def test_passive_appraisal_reads_only_its_audience(stores, tmp_path):
    from tests.test_api_bridge import _api
    from nahida_bot_sdk.messaging import InboundMessage

    memory, _, _ = stores
    service = MemoryService(memory, soft_scope=True)
    await service.store_item(
        "coffee",
        "coffee private",
        ctx=context(),
        metadata={"subject": "current_sender", "sensitivity": "private"},
    )
    await service.store_item(
        "coffee", "coffee public", ctx=context(), metadata={"subject": "current_sender"}
    )
    api, _, _, _ = _api(tmp_path, memory_store=memory, memory_soft_scope=True)
    event = InboundMessage(
        message_id="m1",
        platform="milky",
        chat_id="room",
        user_id="alice",
        text="coffee",
        raw_event={},
        is_group=True,
    )
    sections = await api.memory_context((event,), session_id="milky:group:room")
    assert [item.content for item in sections["person"]] == ["coffee public"]


async def test_reader_cannot_observe_uncommitted_confirmation(stores, monkeypatch):
    import asyncio

    memory, identity, _ = stores
    candidate = await identity.mutations.propose(
        "milky:user:alice", "telegram:user:alice", {}
    )
    reached = asyncio.Event()
    release = asyncio.Event()
    original = identity.mutations.audit

    async def fail_final_audit(action, *args):
        if action == "identity.confirmed":
            reached.set()
            await release.wait()
            raise RuntimeError("rollback")
        await original(action, *args)

    monkeypatch.setattr(identity.mutations, "audit", fail_final_audit)
    writer = asyncio.create_task(
        identity.mutations.resolve(
            candidate["candidate_id"], "telegram:user:alice", confirm=True
        )
    )
    await reached.wait()
    reader = asyncio.create_task(memory.memory_identity("telegram:user:alice"))
    resolver = asyncio.create_task(identity.resolve_account("telegram:user:alice"))
    await asyncio.sleep(0)
    assert not reader.done() and not resolver.done()
    release.set()
    with pytest.raises(RuntimeError):
        await writer
    assert await reader == (None, ["telegram:user:alice"])
    assert await resolver == (None, "none")


async def test_appraisal_call_receives_structured_memory_and_persists_its_influence(
    stores,
):
    import json
    from types import SimpleNamespace
    from nahida_bot.plugins.conversation_joiner.social import (
        SocialContinuity,
        SocialTurn,
    )
    from nahida_bot_sdk.messaging import InboundMessage

    memory, _, _ = stores
    item = MemoryService(memory).prepare_item(
        "friend",
        "Alice enjoys welcoming Bob",
        ctx=context(),
        metadata={"subject": "current_sender", "kind": "impression", "confidence": 0.7},
    )
    ref = MemoryRef(
        key=item.item_id, content=item.content, metadata=memory_ref_metadata(item)
    )
    response = {
        "social": {
            "feeling": {
                "text": "Glad Bob returned",
                "label": "happy",
                "intensity": 0.3,
                "subject_id": "self",
                "evidence_ids": ["m1"],
                "memory_ids": [item.item_id],
            },
            "thoughts": [],
        }
    }
    api = SimpleNamespace(
        plugin_data_get=AsyncMock(return_value=None),
        plugin_data_set=AsyncMock(),
        memory_context=AsyncMock(return_value={"person": [ref]}),
        llm_chat=AsyncMock(return_value=SimpleNamespace(content=json.dumps(response))),
        logger=SimpleNamespace(
            debug=lambda *a, **k: None, warning=lambda *a, **k: None
        ),
    )
    social = SocialContinuity(api, InnerThoughtsConfig(enabled=True))
    message = InboundMessage(
        message_id="m1",
        platform="milky",
        chat_id="room",
        user_id="alice",
        text="Bob is back",
        raw_event={},
        is_group=True,
    )
    turn = SocialTurn(
        "milky:group:room", "milky:group:room", "Nahida", (message,), "m1"
    )
    await social.chat(turn, system="Appraise", prompt="Current event", model="cheap")
    messages = api.llm_chat.call_args.args[0]
    data = json.loads(messages[1]["content"].split("Continuity data:\n")[1])
    assert data["durable_memories"][0]["kind"] == "impression"
    assert data["durable_memories"][0]["confidence"] == 0.7
    saved = api.plugin_data_set.call_args.args[1]
    assert saved["feeling"]["memory_ids"] == [item.item_id]
    assert api.llm_chat.await_count == 1
    assert api.llm_chat.call_args.kwargs["tools"] == []


async def test_secret_like_never_follows_a_confirmed_person(stores):
    from nahida_bot.agent.memory.attribution import EvidenceRef

    memory, identity, _ = stores
    result = await link_pair(identity)
    source = context(person=result["person_id"])
    other = context("telegram:user:alice", person=result["person_id"])
    service = MemoryService(memory, soft_scope=True)
    item_id = await service.store_item(
        "coffee",
        "coffee sensitive matter",
        ctx=source,
        metadata={"subject": "current_sender", "sensitivity": "secret_like"},
    )
    assert (await memory.get_items_by_ids([item_id]))[0].scope_type == "chat"
    # Legacy rows that were already in a shared Person scope also remain local.
    await memory.append_item(
        content="coffee old matter",
        scope_type="person",
        scope_id=result["person_id"],
        sensitivity="secret_like",
        metadata={
            "attribution": MemoryAttribution(
                MemorySubject("account", "milky:user:alice"),
                (EvidenceRef.from_context(source),),
            ).to_dict()
        },
    )
    assert len(await service.search_items_cascade("coffee", ctx=source)) == 2
    assert not await service.search_items_cascade("coffee", ctx=other)
    assert not (await service.context_items(ctx=other))["person"]


@pytest.mark.parametrize(
    "selector,kind",
    [
        ("bot", "bot"),
        ("environment", "environment"),
        ("chat", "chat"),
        ("current_sender", "account"),
    ],
)
async def test_explicit_evidence_preserves_selected_subject(stores, selector, kind):
    from nahida_bot.agent.memory.models import ConversationTurn

    memory, _, _ = stores
    ctx = context()
    await memory.ensure_session(ctx.session_id)
    turn_id = await memory.append_turn(
        ctx.session_id,
        ConversationTurn(
            "user",
            "Remember this durable context",
            metadata={"message_context": {"sender_id": "alice"}},
        ),
    )
    item_id = await MemoryService(memory).store_item(
        "context",
        "durable context",
        ctx=ctx,
        metadata={"subject": selector, "evidence_turn_ids": [turn_id]},
    )
    item = (await memory.get_items_by_ids([item_id]))[0]
    assert MemoryAttribution.parse(item.metadata).subject.kind == kind


def test_config_cannot_seed_two_accounts_on_the_same_channel():
    from nahida_bot.core.config import IdentityPersonSeed

    with pytest.raises(ValueError, match="one account per channel"):
        IdentityPersonSeed.model_validate(
            {
                "person_id": "owner",
                "accounts": [
                    {"channel": "milky", "platform_account_id": "a"},
                    {"channel": "milky", "platform_account_id": "b"},
                ],
            }
        )


async def test_person_upsert_preserves_reactivation_and_audits_status(stores):
    from nahida_bot.identity.models import Person

    _, identity, _ = stores
    await identity.upsert_person(Person(person_id="alice", status="inactive"))
    await IdentityManager(identity).create_or_update_person(
        person_id="alice", display_name="Alice", actor="operator"
    )
    assert (await identity.list_people())[0].status == "active"
    audit = (await identity.list_audit())[0]
    assert audit.before["status"] == "inactive"
    assert audit.after["status"] == "active"


async def test_person_browse_can_target_a_group_member_without_reading_their_private_memory(
    stores, tmp_path
):
    from tests.test_api_bridge import _api
    from nahida_bot.core.context import current_session

    memory, _, _ = stores
    service = MemoryService(memory, soft_scope=True)
    for sensitivity in ("public", "private"):
        await service.store_item(
            "coffee",
            f"Bob coffee {sensitivity}",
            ctx=context("milky:user:bob"),
            metadata={"subject": "current_sender", "sensitivity": sensitivity},
        )
    api, _, _, _ = _api(tmp_path, memory_store=memory, memory_soft_scope=True)
    token = current_session.set(context(chat="milky:group:room"))
    try:
        refs = await api.memory_browse("person", account_key="milky:user:bob")
        assert [ref.content for ref in refs] == ["Bob coffee public"]
    finally:
        current_session.reset(token)
