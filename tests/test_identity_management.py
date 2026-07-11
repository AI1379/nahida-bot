"""Audited identity management tests for issue #7 Phase 4."""

from __future__ import annotations

import pytest

from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.identity.management import IdentityManagementError, IdentityManager
from nahida_bot.identity.sqlite import SQLiteIdentityStore


@pytest.fixture
async def identity_store() -> SQLiteIdentityStore:
    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    yield SQLiteIdentityStore(engine)
    await engine.close()


@pytest.mark.asyncio
async def test_person_link_unlink_are_audited(
    identity_store: SQLiteIdentityStore,
) -> None:
    manager = IdentityManager(identity_store)

    await manager.create_or_update_person(
        person_id="owner",
        display_name="Owner",
        actor="test:admin",
    )
    await manager.link_account(
        account_key="desktop:user:owner",
        person_id="owner",
        actor="test:admin",
    )
    assert await identity_store.resolve_account("desktop:user:owner") == (
        "owner",
        "manual_link",
    )
    assert await manager.unlink_account(
        account_key="desktop:user:owner",
        actor="test:admin",
    )

    audit = list(reversed(await identity_store.list_audit()))
    assert [entry.action for entry in audit] == [
        "person.upsert",
        "account.link",
        "account.unlink",
    ]
    assert all(entry.actor == "test:admin" for entry in audit)


@pytest.mark.asyncio
async def test_link_requires_existing_person(
    identity_store: SQLiteIdentityStore,
) -> None:
    manager = IdentityManager(identity_store)

    with pytest.raises(IdentityManagementError, match="does not exist"):
        await manager.link_account(
            account_key="desktop:user:owner",
            person_id="missing",
            actor="test:admin",
        )


@pytest.mark.asyncio
async def test_relink_audit_preserves_previous_person(
    identity_store: SQLiteIdentityStore,
) -> None:
    manager = IdentityManager(identity_store)
    for person_id in ("alice", "bob"):
        await manager.create_or_update_person(
            person_id=person_id,
            actor="test:admin",
        )
    await manager.link_account(
        account_key="desktop:user:shared",
        person_id="alice",
        actor="test:admin",
    )
    await manager.link_account(
        account_key="desktop:user:shared",
        person_id="bob",
        actor="test:admin",
    )

    audit = await identity_store.list_audit()
    relink = next(
        entry
        for entry in audit
        if entry.action == "account.link" and entry.after.get("person_id") == "bob"
    )
    assert relink.before.get("person_id") == "alice"
