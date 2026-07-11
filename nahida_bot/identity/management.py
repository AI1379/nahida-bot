"""Audited identity management service (issue #7 Phase 4)."""

from __future__ import annotations

from nahida_bot.identity.models import (
    AccountKey,
    AccountLink,
    IdentityAuditEntry,
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
        existing = {
            person.person_id: person for person in await self._store.list_people()
        }
        before_person = existing.get(clean_id)
        person = Person(person_id=clean_id, display_name=display_name.strip())
        await self._store.upsert_person(person)
        await self._store.record_audit(
            IdentityAuditEntry(
                action="person.upsert",
                actor=actor,
                target_type="person",
                target_id=clean_id,
                before=_person_dict(before_person),
                after=_person_dict(person),
            )
        )
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
        before_person_id, before_source = await self._store.resolve_account(
            canonical_key
        )
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
        await self._store.upsert_account_link(link)
        await self._store.record_audit(
            IdentityAuditEntry(
                action="account.link",
                actor=actor,
                target_type="account",
                target_id=canonical_key,
                before={
                    "person_id": before_person_id or "",
                    "verification": before_source,
                },
                after={"person_id": clean_person_id, "verification": "manual_link"},
            )
        )
        return link

    async def unlink_account(self, *, account_key: str, actor: str) -> bool:
        canonical_key = str(AccountKey.parse(account_key.strip()))
        before_person_id, before_source = await self._store.resolve_account(
            canonical_key
        )
        changed = await self._store.unlink_account(canonical_key)
        if changed:
            await self._store.record_audit(
                IdentityAuditEntry(
                    action="account.unlink",
                    actor=actor,
                    target_type="account",
                    target_id=canonical_key,
                    before={
                        "person_id": before_person_id or "",
                        "verification": before_source,
                    },
                    after={"status": "inactive"},
                )
            )
        return changed


def _person_dict(person: Person | None) -> dict[str, object]:
    if person is None:
        return {}
    return {
        "person_id": person.person_id,
        "display_name": person.display_name,
        "status": person.status,
    }


__all__ = ["IdentityManagementError", "IdentityManager"]
