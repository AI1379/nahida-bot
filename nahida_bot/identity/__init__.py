"""Person/account identity system (issue #7).

Phase 0+1 delivers identity models, a SQLite-backed store, a resolver that
populates :class:`SessionContext` from each inbound message, and ``/identity
whoami``. Identity-aware memory read/write (Phase 2/3), management commands
and WebUI (Phase 4), and self-service linking (Phase 5) build on this base.
"""

from nahida_bot.identity.models import (
    AccountKey,
    AccountLink,
    IdentityResolution,
    Person,
    ParticipantObservation,
)
from nahida_bot.identity.resolver import IdentityResolver, account_key_from_inbound
from nahida_bot.identity.sqlite import SQLiteIdentityStore
from nahida_bot.identity.store import IdentityStore

__all__ = [
    "AccountKey",
    "AccountLink",
    "IdentityResolution",
    "IdentityResolver",
    "IdentityStore",
    "ParticipantObservation",
    "Person",
    "SQLiteIdentityStore",
    "account_key_from_inbound",
]
