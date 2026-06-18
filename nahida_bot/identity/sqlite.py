"""SQLite-backed :class:`IdentityStore` over :class:`SQLiteIdentityRepository`.

This façade maps between the :mod:`nahida_bot.identity.models` types and the
flat repo parameters, so consumers (resolver, config seed, commands) work in
domain objects.
"""

from __future__ import annotations

from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.db.repositories.sqlite_identity_repo import SQLiteIdentityRepository
from nahida_bot.identity.models import AccountLink, Person, ParticipantObservation


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
        await self._repo.upsert_account_link(
            account_key=link.account_key,
            person_id=link.person_id,
            channel=link.channel,
            account_type=link.account_type,
            platform_account_id=link.platform_account_id,
            label=link.label,
            status=link.status,
            verification=link.verification,
            linked_by=link.linked_by,
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


def _row_to_account_link(row: dict[str, object]) -> AccountLink:
    return AccountLink(
        account_key=str(row["account_key"]),
        person_id=str(row["person_id"]),
        channel=str(row["channel"]),
        account_type=str(row["account_type"]),
        platform_account_id=str(row["platform_account_id"]),
        label=str(row["label"]),
        status="active",
        verification=str(row["verification"]),  # type: ignore[arg-type]
        linked_by="",
    )
