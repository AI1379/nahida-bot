"""Identity-aware memory read-scope policy (issue #7, Phase 2).

Turns an inbound turn's identity (chat address + linked person + sender account)
into the ordered memory scope cascade used by the read path:

- Private 1:1 chat: ``person -> account -> chat -> global``
- Group / channel / thread: ``chat -> global`` (private sender memory is **not**
  injected by default; ``group_person_memory="allow_private"`` opts in to the
  sender's person/account scopes)
- Legacy / untyped chat: ``global`` only

The identity-off invariant is the safety contract: when identity is disabled or
the sender is unlinked (``person_id is None`` and ``sender_account_key == ""``),
the person/account scopes are omitted and the cascade collapses to the V1
``chat -> global`` (or ``global``-only) behavior byte-for-byte. That keeps the
whole subsystem a pure no-op until ``identity.enabled`` is set.

Visibility / sensitivity filtering of individual items (``visible_only``) is a
Phase 3 concern — items do not carry visibility tags yet — so for Phase 2 the
group cascade is deliberately conservative: only ``allow_private`` injects
sender scopes at all, and even then only non-private items would surface once
Phase 3 tags them. See ``docs/design/person-identity-system.md`` §5 and §10.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from nahida_bot.agent.memory.scope import (
    SCOPE_ID_GLOBAL,
    SCOPE_TYPE_ACCOUNT,
    SCOPE_TYPE_CHAT,
    SCOPE_TYPE_GLOBAL,
    SCOPE_TYPE_PERSON,
)
from nahida_bot.core.chat_address import SessionKey

if TYPE_CHECKING:
    from nahida_bot.core.context import SessionContext
    from nahida_bot.core.chat_address import ChatAddress

GroupPersonMemoryPolicy = Literal["off", "visible_only", "allow_private"]

#: Conservative default for group chats — never inject private sender memory.
DEFAULT_GROUP_PERSON_MEMORY: GroupPersonMemoryPolicy = "visible_only"


@dataclass(frozen=True, slots=True)
class MemoryReadRequest:
    """Inputs to a memory read-scope cascade.

    ``chat_scope_id`` is the ``chat`` scope id (the typed ChatAddress's
    ``chat_key``); empty for legacy/untyped chats, which resolve to global only.
    ``person_id`` / ``sender_account_key`` come from :class:`IdentityResolution`
    and are empty/None when identity is off or the account is unlinked.
    """

    target_type: str
    chat_scope_id: str
    person_id: str | None = None
    sender_account_key: str = ""
    group_person_memory: GroupPersonMemoryPolicy = DEFAULT_GROUP_PERSON_MEMORY


def resolve_memory_read_scopes(
    req: MemoryReadRequest,
) -> list[tuple[str, str]]:
    """Return the ordered ``(scope_type, scope_id)`` cascade for a read.

    Order is priority: an earlier scope fills the result budget before a later
    one is searched. The caller wraps each pair in a ``RetrievalScope``.
    """
    # Legacy / untyped chat has no chat scope — global only (V1 behavior).
    if not req.chat_scope_id or req.target_type == "unknown":
        return [(SCOPE_TYPE_GLOBAL, SCOPE_ID_GLOBAL)]

    scopes: list[tuple[str, str]] = []
    if req.target_type == "private":
        # Private 1:1: the sender is the audience, so personal memory is safe.
        if req.person_id:
            scopes.append((SCOPE_TYPE_PERSON, req.person_id))
        if req.sender_account_key:
            scopes.append((SCOPE_TYPE_ACCOUNT, req.sender_account_key))
        scopes.append((SCOPE_TYPE_CHAT, req.chat_scope_id))
        scopes.append((SCOPE_TYPE_GLOBAL, SCOPE_ID_GLOBAL))
        return scopes

    # Group / channel / thread: chat rules + global knowledge. Private sender
    # memory is injected only under an explicit ``allow_private`` opt-in; the
    # default (off / visible_only) keeps private 1:1 facts out of group turns.
    scopes.append((SCOPE_TYPE_CHAT, req.chat_scope_id))
    if req.group_person_memory == "allow_private":
        if req.person_id:
            scopes.append((SCOPE_TYPE_PERSON, req.person_id))
        if req.sender_account_key:
            scopes.append((SCOPE_TYPE_ACCOUNT, req.sender_account_key))
    scopes.append((SCOPE_TYPE_GLOBAL, SCOPE_ID_GLOBAL))
    return scopes


def memory_read_request_from_context(
    ctx: SessionContext | None,
    session_id: str,
    *,
    group_person_memory: GroupPersonMemoryPolicy = DEFAULT_GROUP_PERSON_MEMORY,
) -> MemoryReadRequest:
    """Build a :class:`MemoryReadRequest` from the live session context.

    Prefers the typed ``ChatAddress`` on the context (it carries the configured
    channel id); falls back to parsing ``session_id`` for typed chats when the
    context is absent or lacks an address (e.g. tests, some cron paths).
    Identity fields come from the context and stay empty when identity is off.
    """
    target_type = "unknown"
    chat_scope_id = ""
    person_id: str | None = None
    sender_account_key = ""

    if ctx is not None:
        person_id = ctx.person_id
        sender_account_key = ctx.sender_account_key
        address: ChatAddress | None = ctx.chat_address
        if address is not None:
            target_type = address.target_type
            chat_scope_id = address.chat_key if address.is_typed else ""

    # Fall back to the session id for a typed chat when the context did not
    # carry a typed address. Legacy / malformed ids leave the global fallback.
    if not chat_scope_id and session_id:
        try:
            key = SessionKey.parse(session_id)
        except (ValueError, TypeError):
            pass
        else:
            address = getattr(key, "address", None)
            if address is not None and address.is_typed:
                target_type = address.target_type
                chat_scope_id = address.chat_key

    return MemoryReadRequest(
        target_type=target_type,
        chat_scope_id=chat_scope_id,
        person_id=person_id,
        sender_account_key=sender_account_key,
        group_person_memory=group_person_memory,
    )
