"""Memory scope resolution helpers.

Durable memory items are isolated by scope. This module is the single source
of truth for scope constants and for deriving a scope from a session id.

V1 has two scopes:

- ``global``: shared knowledge visible to every session.
- ``chat``: per-ChatAddress personal memory (preferences, facts, tasks).

Legacy / untyped sessions resolve to ``global`` so existing behavior is
preserved byte-for-byte. ``person`` / ``account`` / ``collection`` scopes are
reserved for future work (identity system #7, knowledge base) and are not
produced here yet. See ``docs/design/memory-scoping.md``.
"""

from __future__ import annotations

from nahida_bot.core.chat_address import ChatAddress, SessionKey

SCOPE_TYPE_GLOBAL = "global"
SCOPE_TYPE_CHAT = "chat"
SCOPE_ID_GLOBAL = "__global__"

CHAT_SCOPED_KINDS = frozenset({"preference", "fact", "task"})
GLOBAL_SCOPED_KINDS = frozenset({"decision", "procedure", "warning", "summary"})


def scope_for_kind(kind: str) -> str:
    """Return the default scope type for a memory kind."""
    return SCOPE_TYPE_CHAT if kind in CHAT_SCOPED_KINDS else SCOPE_TYPE_GLOBAL


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
