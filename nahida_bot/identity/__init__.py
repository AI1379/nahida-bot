"""Person/account identity system (issue #7).

Phase 0+1 delivers identity models, a SQLite-backed store, a resolver that
populates :class:`SessionContext` from each inbound message, and ``/identity
whoami``. Identity-aware memory read/write (Phase 2/3), management commands
and WebUI (Phase 4), and self-service linking (Phase 5) build on this base.
"""

from nahida_bot.identity.authorization import AuthorizationGate, NotAuthorized
from nahida_bot.identity.management import IdentityManagementError, IdentityManager
from nahida_bot.identity.models import (
    AccountKey,
    AccountLink,
    IdentityResolution,
    IdentityAuditEntry,
    Person,
    ParticipantObservation,
)
from nahida_bot.identity.policy import (
    MemoryReadRequest,
    MemoryWriteRequest,
    memory_read_request_from_context,
    memory_write_request_from_context,
    resolve_memory_read_scopes,
    resolve_memory_write_scope,
)
from nahida_bot.identity.resolver import IdentityResolver, account_key_from_inbound
from nahida_bot.identity.sqlite import SQLiteIdentityStore
from nahida_bot.identity.store import IdentityStore

__all__ = [
    "AccountKey",
    "AccountLink",
    "AuthorizationGate",
    "IdentityResolution",
    "IdentityAuditEntry",
    "IdentityManagementError",
    "IdentityManager",
    "IdentityResolver",
    "IdentityStore",
    "MemoryReadRequest",
    "MemoryWriteRequest",
    "NotAuthorized",
    "ParticipantObservation",
    "Person",
    "SQLiteIdentityStore",
    "account_key_from_inbound",
    "memory_read_request_from_context",
    "memory_write_request_from_context",
    "resolve_memory_read_scopes",
    "resolve_memory_write_scope",
]
