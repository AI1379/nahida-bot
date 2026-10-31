"""Audited identity management service (issue #7 Phase 4)."""

from __future__ import annotations

from nahida_bot.identity.models import (
    AccountKey,
    AccountLink,
    Person,
)
from nahida_bot.identity.store import IdentityStore


class IdentityManagementError(ValueError):
    """Invalid identity-management operation."""


class IdentityManager:
    """Single audited mutation boundary for persons and account links."""

    def __init__(self, store: IdentityStore) -> None:
        self._store = store

    async def create_or_update_person(
        self,
        *,
        person_id: str,
        display_name: str = "",
        actor: str,
    ) -> Person:
        clean_id = person_id.strip()
        if not clean_id:
            raise IdentityManagementError("person_id must not be empty")
        person = Person(person_id=clean_id, display_name=display_name.strip())
        await self._store.save_person_audited(person, actor)
        return person

    async def link_account(
        self,
        *,
        account_key: str,
        person_id: str,
        label: str = "",
        actor: str,
    ) -> AccountLink:
        canonical_key = str(AccountKey.parse(account_key.strip()))
        clean_person_id = person_id.strip()
        people = {person.person_id for person in await self._store.list_people()}
        if clean_person_id not in people:
            raise IdentityManagementError(f"person {clean_person_id!r} does not exist")
        parsed = AccountKey.parse(canonical_key)
        link = AccountLink(
            account_key=canonical_key,
            person_id=clean_person_id,
            channel=parsed.channel,
            platform_account_id=parsed.platform_user_id,
            label=label.strip(),
            verification="manual_link",
            linked_by=actor,
        )
        try:
            await self._store.link_account_audited(link, actor)
        except ValueError as exc:
            raise IdentityManagementError(str(exc)) from exc
        return link

    async def unlink_account(self, *, account_key: str, actor: str) -> bool:
        canonical_key = str(AccountKey.parse(account_key.strip()))
        return await self._store.unlink_account_audited(canonical_key, actor)


__all__ = ["IdentityManagementError", "IdentityManager"]
