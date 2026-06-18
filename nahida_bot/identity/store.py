"""Identity store protocol — the contract identity consumers depend on.

Phase 0+1 covers resolution, config-seeded links, and observation recording.
Management mutations (unlink/list) are included for ``/identity`` commands and
tests; admin create/link commands arrive in Phase 4.
"""

from __future__ import annotations

from typing import Protocol

from nahida_bot.identity.models import AccountLink, Person, ParticipantObservation


class IdentityStore(Protocol):
    """Read/write access to persons, account links, and observations."""

    async def resolve_account(self, account_key: str) -> tuple[str | None, str]:
        """Return ``(person_id, verification_source)`` for an active link.

        ``(None, "none")`` when the account has no active person binding.
        """
        ...

    async def upsert_person(self, person: Person) -> None:
        """Insert or update a person."""
        ...

    async def upsert_account_link(self, link: AccountLink) -> None:
        """Bind an account to a person (idempotent upsert by account_key)."""
        ...

    async def list_accounts(self, person_id: str) -> list[AccountLink]:
        """Return active account links for a person."""
        ...

    async def unlink_account(self, account_key: str) -> bool:
        """Mark an account link inactive. Returns whether a row was affected."""
        ...

    async def record_observation(self, observation: ParticipantObservation) -> None:
        """Upsert how an account last appeared in a chat."""
        ...
