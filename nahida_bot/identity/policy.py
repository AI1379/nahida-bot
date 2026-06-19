"""Identity-aware memory scope policy (issue #7).

Owns BOTH sides of the memory-scope decision:

- **Read cascade** (Phase 2): turn an inbound turn's identity into the ordered
  scope list the read path cascades through — private 1:1 is
  ``person -> account -> chat -> global``; group is ``chat -> global`` (the
  sender's private memory is injected only under ``allow_private``); legacy is
  ``global`` only.
- **Write scope** (Phase 3): pick the single scope a memory item of a given
  ``kind`` is written to — personal kinds go to ``person`` (linked) or
  ``account`` (unlinked) or ``chat`` (identity off); global kinds go to
  ``global``.

Identity-off invariant (the safety contract): when identity is disabled or the
sender is unlinked (``person_id is None`` and ``sender_account_key == ""``),
both sides collapse to V1 — read becomes ``chat -> global`` (or ``global``-only)
and write becomes ``chat``/``global`` per kind — byte-for-byte. The whole
subsystem stays a no-op until ``identity.enabled`` is set.

**Decoupling:** this module reads identity only to choose a *memory scope*. It
contains no authorization logic. A future action-authorization gate (Phase A)
reads the same ``SessionContext.sender_account_key`` / ``person_id`` for
*permissions*; the two concerns share the identity seam but do not depend on
each other. See ``docs/design/person-identity-system.md`` §2.5.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from nahida_bot.agent.memory.scope import (
    CHAT_SCOPED_KINDS,
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


# ── Write scope (Phase 3) ──────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MemoryWriteRequest:
    """Inputs to a memory write-scope decision.

    Empty identity (``person_id is None`` and ``sender_account_key == ""``) is
    the identity-off state and reproduces V1 (``chat`` for personal kinds in a
    typed chat, else ``global``).
    """

    chat_scope_id: str
    person_id: str | None = None
    sender_account_key: str = ""


def resolve_memory_write_scope(
    req: MemoryWriteRequest,
    kind: str,
) -> tuple[str, str]:
    """Resolve ``(scope_type, scope_id)`` for a memory item of ``kind``.

    - global-scoped kind (decision/procedure/warning/summary) → ``global``
    - personal kind (preference/fact/task):
        ``person_id`` set              → ``person:{person_id}``
        else ``sender_account_key`` set → ``account:{account_key}``
        else ``chat_scope_id`` set      → ``chat:{chat_scope_id}``   (V1)
        else                            → ``global:__global__``      (V1 legacy)

    Personal memory thus follows the *sender*, not the chat address: an owner's
    preference lands in their ``person`` scope so it recalls across all their
    accounts (Phase 2 read cascade), and an unlinked group guest's facts land
    in ``account`` scope so they don't pollute the group's shared ``chat`` scope.
    """
    if kind not in CHAT_SCOPED_KINDS:
        return SCOPE_TYPE_GLOBAL, SCOPE_ID_GLOBAL
    if req.person_id:
        return SCOPE_TYPE_PERSON, req.person_id
    if req.sender_account_key:
        return SCOPE_TYPE_ACCOUNT, req.sender_account_key
    if req.chat_scope_id:
        return SCOPE_TYPE_CHAT, req.chat_scope_id
    return SCOPE_TYPE_GLOBAL, SCOPE_ID_GLOBAL


def memory_write_request_from_context(
    ctx: SessionContext | None,
    session_id: str,
) -> MemoryWriteRequest:
    """Build a :class:`MemoryWriteRequest` from the live session context.

    Identity comes from the context (empty when identity is off / unlinked);
    ``chat_scope_id`` prefers ``ctx.chat_address`` then falls back to parsing
    ``session_id`` for typed chats. Mirrors the read-side helper.
    """
    chat_scope_id = ""
    person_id: str | None = None
    sender_account_key = ""

    if ctx is not None:
        person_id = ctx.person_id
        sender_account_key = ctx.sender_account_key
        address: ChatAddress | None = ctx.chat_address
        if address is not None and address.is_typed:
            chat_scope_id = address.chat_key

    if not chat_scope_id and session_id:
        try:
            key = SessionKey.parse(session_id)
        except (ValueError, TypeError):
            pass
        else:
            address = getattr(key, "address", None)
            if address is not None and address.is_typed:
                chat_scope_id = address.chat_key

    return MemoryWriteRequest(
        chat_scope_id=chat_scope_id,
        person_id=person_id,
        sender_account_key=sender_account_key,
    )
