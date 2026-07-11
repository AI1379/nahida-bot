"""SQLite-backed :class:`IdentityStore` over :class:`SQLiteIdentityRepository`.

This façade maps between the :mod:`nahida_bot.identity.models` types and the
flat repo parameters, so consumers (resolver, config seed, commands) work in
domain objects.
"""

from __future__ import annotations

from datetime import datetime

from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.db.repositories.sqlite_identity_repo import SQLiteIdentityRepository
from nahida_bot.identity.models import (
    AccountLink,
    IdentityAuditEntry,
    Person,
    ParticipantObservation,
)


class SQLiteIdentityStore:
    """IdentityStore implementation sharing the application's DatabaseEngine."""

    def __init__(self, engine: DatabaseEngine) -> None:
        self._repo = SQLiteIdentityRepository(engine)

    async def resolve_account(self, account_key: str) -> tuple[str | None, str]:
        return await self._repo.resolve_account(account_key)

    async def upsert_person(self, person: Person) -> None:
        await self._repo.upsert_person(
            person_id=person.person_id,
            display_name=person.display_name,
            status=person.status,
            metadata=dict(person.metadata),
        )

    async def upsert_account_link(self, link: AccountLink) -> None:
        # channel/account_type/platform_account_id are derived from account_key
        # inside the repo (account_key is the canonical source), so the
        # identity columns can never diverge from the primary key.
        await self._repo.upsert_account_link(
            account_key=link.account_key,
            person_id=link.person_id,
            label=link.label,
            status=link.status,
            verification=link.verification,
            linked_by=link.linked_by,
            metadata=dict(link.metadata),
        )

    async def list_accounts(self, person_id: str) -> list[AccountLink]:
        rows = await self._repo.list_accounts(person_id)
        return [_row_to_account_link(row) for row in rows]

    async def unlink_account(self, account_key: str) -> bool:
        return await self._repo.unlink_account(account_key)

    async def record_observation(self, observation: ParticipantObservation) -> None:
        await self._repo.record_observation(
            account_key=observation.account_key,
            chat_address=observation.chat_address,
            display_name=observation.display_name,
            role_tags=observation.role_tags,
            last_message_id=observation.last_message_id,
        )

    async def list_people(self) -> list[Person]:
        return [_row_to_person(row) for row in await self._repo.list_people()]

    async def list_observations(
        self, *, account_key: str = "", limit: int = 100
    ) -> list[ParticipantObservation]:
        rows = await self._repo.list_observations(
            account_key=account_key,
            limit=limit,
        )
        return [_row_to_observation(row) for row in rows]

    async def record_audit(self, entry: IdentityAuditEntry) -> int:
        return await self._repo.record_audit(
            action=entry.action,
            actor=entry.actor,
            target_type=entry.target_type,
            target_id=entry.target_id,
            before=dict(entry.before),
            after=dict(entry.after),
            metadata=dict(entry.metadata),
        )

    async def list_audit(self, *, limit: int = 100) -> list[IdentityAuditEntry]:
        return [_row_to_audit(row) for row in await self._repo.list_audit(limit=limit)]


def _row_to_account_link(row: dict[str, object]) -> AccountLink:
    metadata = row.get("metadata")
    return AccountLink(
        account_key=str(row["account_key"]),
        person_id=str(row["person_id"]),
        channel=str(row["channel"]),
        account_type=str(row["account_type"]),
        platform_account_id=str(row["platform_account_id"]),
        label=str(row["label"]),
        status="active",
        verification=str(row["verification"]),  # type: ignore[arg-type]
        linked_by=str(row.get("linked_by", "")),
        metadata=dict(metadata) if isinstance(metadata, dict) else {},
    )


def _row_to_person(row: dict[str, object]) -> Person:
    metadata = row.get("metadata")
    return Person(
        person_id=str(row["person_id"]),
        display_name=str(row.get("display_name", "")),
        status=str(row.get("status", "active")),
        metadata=dict(metadata) if isinstance(metadata, dict) else {},
    )


def _row_to_observation(row: dict[str, object]) -> ParticipantObservation:
    role_tags = row.get("role_tags")
    return ParticipantObservation(
        chat_address=str(row["chat_address"]),
        account_key=str(row["account_key"]),
        display_name=str(row.get("display_name", "")),
        role_tags=tuple(str(tag) for tag in role_tags)
        if isinstance(role_tags, list)
        else (),
        first_seen_at=datetime.fromisoformat(str(row["first_seen_at"])),
        last_seen_at=datetime.fromisoformat(str(row["last_seen_at"])),
        last_message_id=str(row.get("last_message_id", "")),
    )


def _row_to_audit(row: dict[str, object]) -> IdentityAuditEntry:
    before = row.get("before")
    after = row.get("after")
    metadata = row.get("metadata")
    return IdentityAuditEntry(
        audit_id=int(str(row["audit_id"])),
        action=str(row["action"]),
        actor=str(row["actor"]),
        target_type=str(row["target_type"]),
        target_id=str(row["target_id"]),
        before=dict(before) if isinstance(before, dict) else {},
        after=dict(after) if isinstance(after, dict) else {},
        metadata=dict(metadata) if isinstance(metadata, dict) else {},
        created_at=datetime.fromisoformat(str(row["created_at"])),
    )
