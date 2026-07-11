"""Tests for the person/account identity system (issue #7, Phase 0+1)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.router import MessageRouter
from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.identity import (
    AccountKey,
    AccountLink,
    IdentityResolver,
    Person,
    SQLiteIdentityStore,
    account_key_from_inbound,
)
from nahida_bot.plugins.base import InboundMessage, SenderContext


# ── AccountKey ───────────────────────────────────────────────


class TestAccountKey:
    def test_str_and_from_parts(self) -> None:
        key = AccountKey.from_parts("milky", "10001")
        assert str(key) == "milky:user:10001"

    def test_parse_roundtrip(self) -> None:
        key = AccountKey.parse("telegram:user:12345")
        assert key.channel == "telegram"
        assert key.platform_user_id == "12345"
        assert str(key) == "telegram:user:12345"

    @pytest.mark.parametrize(
        "bad", ["", "milky", "milky:user:", ":user:1", "milky:10001"]
    )
    def test_parse_rejects_malformed(self, bad: str) -> None:
        with pytest.raises(ValueError):
            AccountKey.parse(bad)


def _inbound(
    *,
    platform: str = "milky",
    user_id: str = "u-legacy",
    sender_context: SenderContext | None = None,
) -> InboundMessage:
    return InboundMessage(
        message_id="m1",
        platform=platform,
        chat_id="10001",
        user_id=user_id,
        text="hi",
        raw_event={},
        sender_context=sender_context,
    )


# ── account_key_from_inbound ─────────────────────────────────


class TestAccountKeyDerivation:
    def test_uses_sender_platform_user_id(self) -> None:
        inbound = _inbound(
            sender_context=SenderContext(
                platform_user_id="10001", display_name="Alice"
            ),
        )
        address = ChatAddress(channel="milky", target_type="private", target_id="10001")
        key = account_key_from_inbound(inbound, address)
        assert key is not None
        assert str(key) == "milky:user:10001"

    def test_falls_back_to_user_id_when_sender_missing(self) -> None:
        inbound = _inbound(user_id="legacy-1", sender_context=None)
        address = ChatAddress(channel="milky", target_type="private", target_id="c")
        key = account_key_from_inbound(inbound, address)
        assert key is not None and str(key) == "milky:user:legacy-1"

    def test_returns_none_without_platform_user_id(self) -> None:
        inbound = _inbound(user_id="", sender_context=None)
        address = ChatAddress(channel="milky", target_type="private", target_id="c")
        assert account_key_from_inbound(inbound, address) is None

    def test_returns_none_without_channel(self) -> None:
        inbound = _inbound(sender_context=SenderContext(platform_user_id="10001"))
        assert account_key_from_inbound(inbound, None) is None


# ── SQLiteIdentityStore ──────────────────────────────────────


@pytest.fixture
async def store() -> SQLiteIdentityStore:
    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    yield SQLiteIdentityStore(engine)
    await engine.close()


@pytest.mark.asyncio
async def test_store_resolves_linked_account(store: SQLiteIdentityStore) -> None:
    await store.upsert_person(Person(person_id="owner", display_name="Arendellian"))
    await store.upsert_account_link(
        AccountLink(
            account_key="milky:user:10001",
            person_id="owner",
            channel="milky",
            platform_account_id="10001",
            verification="config_seed",
        )
    )

    person_id, source = await store.resolve_account("milky:user:10001")
    assert person_id == "owner"
    assert source == "config_seed"


@pytest.mark.asyncio
async def test_store_resolve_unlinked_returns_none(store: SQLiteIdentityStore) -> None:
    assert await store.resolve_account("milky:user:99999") == (None, "none")


@pytest.mark.asyncio
async def test_store_unlink_then_unresolved(store: SQLiteIdentityStore) -> None:
    await store.upsert_person(Person(person_id="owner"))
    await store.upsert_account_link(
        AccountLink(
            account_key="milky:user:10001",
            person_id="owner",
            channel="milky",
            platform_account_id="10001",
        )
    )
    assert await store.unlink_account("milky:user:10001") is True
    assert await store.resolve_account("milky:user:10001") == (None, "none")
    # Idempotent: unlinking again affects nothing.
    assert await store.unlink_account("milky:user:10001") is False


@pytest.mark.asyncio
async def test_store_list_accounts(store: SQLiteIdentityStore) -> None:
    await store.upsert_person(Person(person_id="owner"))
    for channel, pid in [("milky", "10001"), ("telegram", "12345")]:
        await store.upsert_account_link(
            AccountLink(
                account_key=f"{channel}:user:{pid}",
                person_id="owner",
                channel=channel,
                platform_account_id=pid,
            )
        )
    links = await store.list_accounts("owner")
    assert {link.channel for link in links} == {"milky", "telegram"}


@pytest.mark.asyncio
async def test_store_list_accounts_round_trips_provenance(
    store: SQLiteIdentityStore,
) -> None:
    """linked_by and metadata survive a write/read cycle (audit #6)."""
    await store.upsert_person(Person(person_id="owner"))
    await store.upsert_account_link(
        AccountLink(
            account_key="milky:user:10001",
            person_id="owner",
            channel="milky",
            platform_account_id="10001",
            linked_by="admin",
            metadata={"origin": "config", "note": "seed"},
        )
    )
    links = await store.list_accounts("owner")
    assert len(links) == 1
    link = links[0]
    assert link.linked_by == "admin"
    assert link.metadata == {"origin": "config", "note": "seed"}


@pytest.mark.asyncio
async def test_store_account_link_columns_derived_from_account_key(
    store: SQLiteIdentityStore,
) -> None:
    """channel/account_type/platform_account_id come from account_key, not the
    caller-supplied AccountLink fields — the identity columns can't diverge
    from the primary key (audit #3)."""
    await store.upsert_person(Person(person_id="owner"))
    await store.upsert_account_link(
        AccountLink(
            account_key="milky:user:10001",
            person_id="owner",
            channel="would-collide",  # deliberately inconsistent
            account_type="bot",  # deliberately inconsistent
            platform_account_id="99999",  # deliberately inconsistent
        )
    )
    links = await store.list_accounts("owner")
    assert len(links) == 1
    link = links[0]
    # Derived from account_key="milky:user:10001", ignoring the wrong fields.
    assert link.channel == "milky"
    assert link.account_type == "user"
    assert link.platform_account_id == "10001"
    # resolve still works (account_key is the lookup key).
    person_id, _ = await store.resolve_account("milky:user:10001")
    assert person_id == "owner"


@pytest.mark.asyncio
async def test_observation_upsert_is_idempotent(store: SQLiteIdentityStore) -> None:
    from nahida_bot.identity import ParticipantObservation

    obs = ParticipantObservation(
        chat_address="milky:private:10001",
        account_key="milky:user:10001",
        display_name="Alice",
    )
    await store.record_observation(obs)
    await store.record_observation(obs)
    repo = store._repo  # type: ignore[attr-defined]
    assert await repo.count_observations("milky:user:10001") == 1


# ── IdentityResolver ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolver_disabled_is_noop(store: SQLiteIdentityStore) -> None:
    resolver = IdentityResolver(store, enabled=False)
    inbound = _inbound(sender_context=SenderContext(platform_user_id="10001"))
    address = ChatAddress(channel="milky", target_type="private", target_id="10001")
    assert await resolver.resolve(inbound, address, "milky:private:10001") is None


@pytest.mark.asyncio
async def test_resolver_linked(store: SQLiteIdentityStore) -> None:
    await store.upsert_person(Person(person_id="owner", display_name="Alice"))
    await store.upsert_account_link(
        AccountLink(
            account_key="milky:user:10001",
            person_id="owner",
            channel="milky",
            platform_account_id="10001",
            verification="config_seed",
        )
    )
    resolver = IdentityResolver(store, enabled=True)
    inbound = _inbound(sender_context=SenderContext(platform_user_id="10001"))
    address = ChatAddress(channel="milky", target_type="private", target_id="10001")

    identity = await resolver.resolve(inbound, address, "milky:private:10001")

    assert identity is not None
    assert identity.sender_account_key == "milky:user:10001"
    assert identity.person_id == "owner"
    assert identity.confidence == "linked"
    assert identity.source == "config_seed"
    # Observation recorded.
    repo = store._repo  # type: ignore[attr-defined]
    assert await repo.count_observations("milky:user:10001") == 1


@pytest.mark.asyncio
async def test_resolver_unlinked(store: SQLiteIdentityStore) -> None:
    resolver = IdentityResolver(store, enabled=True)
    inbound = _inbound(sender_context=SenderContext(platform_user_id="10001"))
    address = ChatAddress(channel="milky", target_type="private", target_id="10001")

    identity = await resolver.resolve(inbound, address, "milky:private:10001")

    assert identity is not None
    assert identity.person_id is None
    assert identity.confidence == "unlinked"


@pytest.mark.asyncio
async def test_resolver_uses_gateway_approved_actor_override(
    store: SQLiteIdentityStore,
) -> None:
    await store.upsert_person(Person(person_id="owner"))
    await store.upsert_account_link(
        AccountLink(
            account_key="desktop:user:owner",
            person_id="owner",
            channel="desktop",
            platform_account_id="owner",
        )
    )
    resolver = IdentityResolver(store, enabled=True)
    inbound = _inbound(
        platform="conversation",
        user_id="must-not-be-used",
        sender_context=SenderContext(platform_user_id="must-not-be-used"),
    )
    address = ChatAddress(
        channel="conversation",
        target_type="private",
        target_id="owner-desktop",
    )

    identity = await resolver.resolve(
        inbound,
        address,
        "conversation:private:owner-desktop",
        account_key_override="desktop:user:owner",
    )

    assert identity is not None
    assert identity.sender_account_key == "desktop:user:owner"
    assert identity.person_id == "owner"


@pytest.mark.asyncio
async def test_resolver_no_account_returns_none(store: SQLiteIdentityStore) -> None:
    resolver = IdentityResolver(store, enabled=True)
    inbound = _inbound(user_id="", sender_context=None)
    address = ChatAddress(channel="milky", target_type="private", target_id="c")
    assert await resolver.resolve(inbound, address, "milky:private:c") is None


# ── Router SessionContext wiring ─────────────────────────────


def _minimal_router(resolver: IdentityResolver | None) -> MessageRouter:
    """A router with stubbed deps; only ``_build_session_context`` is exercised."""
    return MessageRouter(
        event_bus=MagicMock(),
        command_registry=MagicMock(),
        command_matcher=MagicMock(),
        channel_registry=MagicMock(),
        runner=None,
        workspace_manager=None,
        config=None,
        identity_resolver=resolver,
    )


@pytest.mark.asyncio
async def test_router_builds_session_context_with_identity(
    store: SQLiteIdentityStore,
) -> None:
    await store.upsert_person(Person(person_id="owner"))
    await store.upsert_account_link(
        AccountLink(
            account_key="milky:user:10001",
            person_id="owner",
            channel="milky",
            platform_account_id="10001",
        )
    )
    router = _minimal_router(IdentityResolver(store, enabled=True))
    inbound = _inbound(
        sender_context=SenderContext(platform_user_id="10001", display_name="Alice")
    )
    address = ChatAddress(channel="milky", target_type="private", target_id="10001")

    ctx = await router._build_session_context(
        inbound, address, "milky:private:10001", None
    )

    assert ctx.sender_account_key == "milky:user:10001"
    assert ctx.person_id == "owner"


@pytest.mark.asyncio
async def test_router_leaves_identity_empty_without_resolver() -> None:
    router = _minimal_router(None)
    inbound = _inbound(sender_context=SenderContext(platform_user_id="10001"))
    address = ChatAddress(channel="milky", target_type="private", target_id="10001")

    ctx = await router._build_session_context(
        inbound, address, "milky:private:10001", None
    )

    assert ctx.sender_account_key == ""
    assert ctx.person_id is None


@pytest.mark.asyncio
async def test_router_leaves_identity_empty_when_disabled(
    store: SQLiteIdentityStore,
) -> None:
    router = _minimal_router(IdentityResolver(store, enabled=False))
    inbound = _inbound(sender_context=SenderContext(platform_user_id="10001"))
    address = ChatAddress(channel="milky", target_type="private", target_id="10001")

    ctx = await router._build_session_context(
        inbound, address, "milky:private:10001", None
    )

    assert ctx.sender_account_key == ""
    assert ctx.person_id is None
