"""Memory scope resolution helpers.

Durable memory items are isolated by scope. This module is the single source
of truth for scope constants and for deriving a scope from a session id.

V1 has two scopes:

- ``global``: shared knowledge visible to every session.
- ``chat``: per-ChatAddress personal memory (preferences, facts, tasks).

Legacy / untyped sessions resolve to ``global`` so existing behavior is
preserved byte-for-byte. The identity system (issue #7) adds two more scopes:

- ``person``: a real chat counterpart spanning multiple platform accounts.
- ``account``: a single unlinked platform account's personal memory.

``person`` / ``account`` scope *ids* are not derived here — they come from the
identity resolver (``nahida_bot.identity.policy``), which builds the read
cascade (person -> account -> chat -> global). This module only owns the scope
*type* string constants so they have one source of truth on the DB side. See
``docs/design/memory-scoping.md`` and ``docs/design/person-identity-system.md``.
"""

from __future__ import annotations

from nahida_bot.core.chat_address import ChatAddress, SessionKey

SCOPE_TYPE_GLOBAL = "global"
SCOPE_TYPE_CHAT = "chat"
SCOPE_TYPE_PERSON = "person"
SCOPE_TYPE_ACCOUNT = "account"
# KB / shared-work scopes (knowledge-base.md §5.1). The enum is pinned here as
# the KB/Memory/identity shared contract; KB collections physically isolate by
# table so these are reserved (not yet produced as memory scope ids).
SCOPE_TYPE_COLLECTION = "collection"
SCOPE_TYPE_WORKSPACE = "workspace"
SCOPE_ID_GLOBAL = "__global__"

CHAT_SCOPED_KINDS = frozenset({"preference", "fact", "task"})
GLOBAL_SCOPED_KINDS = frozenset({"decision", "procedure", "warning", "summary"})
MEMORY_KINDS = CHAT_SCOPED_KINDS | GLOBAL_SCOPED_KINDS


def scope_for_kind(kind: str) -> str:
    """Return the conservative default scope type for a memory kind.

    Content kind no longer implies a global audience. Known kinds default to
    the current chat; explicit audience policy may promote eligible items.
    """
    return SCOPE_TYPE_CHAT if kind in MEMORY_KINDS else SCOPE_TYPE_GLOBAL


def chat_scope_id(address: ChatAddress) -> str:
    """Return the ``chat`` scope id for a typed ChatAddress."""
    return address.chat_key


def resolve_scope_from_session(session_id: str) -> tuple[str, str]:
    """Resolve ``(scope_type, scope_id)`` for a session id.

    Typed ChatAddress sessions (private chats, groups, channels) map to a
    ``chat`` scope keyed by the address's chat key. Legacy / untyped / cron /
    malformed sessions fall back to the global scope. A bad ``session_id``
    never raises — it resolves to global so consolidation stays robust.
    """
    if not session_id:
        return SCOPE_TYPE_GLOBAL, SCOPE_ID_GLOBAL
    try:
        key = SessionKey.parse(session_id)
    except (ValueError, TypeError):
        return SCOPE_TYPE_GLOBAL, SCOPE_ID_GLOBAL
    address = getattr(key, "address", None)
    if address is not None and address.is_typed:
        return SCOPE_TYPE_CHAT, address.chat_key
    return SCOPE_TYPE_GLOBAL, SCOPE_ID_GLOBAL
