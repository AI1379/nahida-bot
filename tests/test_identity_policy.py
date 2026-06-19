"""Tests for the identity-aware memory read-scope policy (issue #7, Phase 2)."""

from __future__ import annotations

from nahida_bot.agent.memory.scope import SCOPE_ID_GLOBAL
from nahida_bot.core.chat_address import ChatAddress, TargetType
from nahida_bot.core.context import SessionContext
from nahida_bot.identity.policy import (
    MemoryReadRequest,
    MemoryWriteRequest,
    memory_read_request_from_context,
    memory_write_request_from_context,
    resolve_memory_read_scopes,
    resolve_memory_write_scope,
)

GLOBAL = ("global", SCOPE_ID_GLOBAL)
PRIVATE_CHAT = "milky:private:10001"
GROUP_CHAT = "milky:group:20001"
ACCOUNT = "milky:user:10001"


# ── resolve_memory_read_scopes ───────────────────────────────


def test_private_linked_cascades_person_account_chat_global() -> None:
    scopes = resolve_memory_read_scopes(
        MemoryReadRequest(
            target_type="private",
            chat_scope_id=PRIVATE_CHAT,
            person_id="owner",
            sender_account_key=ACCOUNT,
        )
    )
    assert scopes == [
        ("person", "owner"),
        ("account", ACCOUNT),
        ("chat", PRIVATE_CHAT),
        GLOBAL,
    ]


def test_private_unlinked_collapses_to_v1_chat_global() -> None:
    """Identity off / unlinked: no person/account scopes — V1 behavior."""
    scopes = resolve_memory_read_scopes(
        MemoryReadRequest(
            target_type="private",
            chat_scope_id=PRIVATE_CHAT,
        )
    )
    assert scopes == [("chat", PRIVATE_CHAT), GLOBAL]


def test_private_person_without_account_skips_account_scope() -> None:
    scopes = resolve_memory_read_scopes(
        MemoryReadRequest(
            target_type="private",
            chat_scope_id=PRIVATE_CHAT,
            person_id="owner",
        )
    )
    assert scopes == [("person", "owner"), ("chat", PRIVATE_CHAT), GLOBAL]


def test_group_default_never_injects_private_sender_memory() -> None:
    """Group chats default to chat + global only — no private sender memory."""
    scopes = resolve_memory_read_scopes(
        MemoryReadRequest(
            target_type="group",
            chat_scope_id=GROUP_CHAT,
            person_id="owner",
            sender_account_key=ACCOUNT,
        )
    )
    assert scopes == [("chat", GROUP_CHAT), GLOBAL]


def test_group_allow_private_opts_into_sender_scopes() -> None:
    scopes = resolve_memory_read_scopes(
        MemoryReadRequest(
            target_type="group",
            chat_scope_id=GROUP_CHAT,
            person_id="owner",
            sender_account_key=ACCOUNT,
            group_person_memory="allow_private",
        )
    )
    assert scopes == [
        ("chat", GROUP_CHAT),
        ("person", "owner"),
        ("account", ACCOUNT),
        GLOBAL,
    ]


def test_group_off_does_not_inject_sender_scopes() -> None:
    scopes = resolve_memory_read_scopes(
        MemoryReadRequest(
            target_type="group",
            chat_scope_id=GROUP_CHAT,
            person_id="owner",
            sender_account_key=ACCOUNT,
            group_person_memory="off",
        )
    )
    assert scopes == [("chat", GROUP_CHAT), GLOBAL]


def test_legacy_untyped_session_is_global_only() -> None:
    assert resolve_memory_read_scopes(
        MemoryReadRequest(target_type="unknown", chat_scope_id="")
    ) == [GLOBAL]


def test_empty_chat_scope_id_is_global_only_even_if_typed_label() -> None:
    assert resolve_memory_read_scopes(
        MemoryReadRequest(target_type="private", chat_scope_id="")
    ) == [GLOBAL]


# ── memory_read_request_from_context ─────────────────────────


def _ctx(
    *,
    target_type: TargetType = "private",
    target_id: str = "10001",
    session_id: str = PRIVATE_CHAT,
    person_id: str | None = None,
    account_key: str = "",
) -> SessionContext:
    address = ChatAddress(channel="milky", target_type=target_type, target_id=target_id)
    return SessionContext(
        platform="milky",
        chat_id=target_id,
        session_id=session_id,
        chat_address=address,
        person_id=person_id,
        sender_account_key=account_key,
    )


def test_context_request_uses_address_and_identity_fields() -> None:
    req = memory_read_request_from_context(
        _ctx(person_id="owner", account_key=ACCOUNT), PRIVATE_CHAT
    )
    assert req.target_type == "private"
    assert req.chat_scope_id == PRIVATE_CHAT
    assert req.person_id == "owner"
    assert req.sender_account_key == ACCOUNT


def test_context_request_falls_back_to_session_id_without_address() -> None:
    """No chat_address on the context -> parse the typed session id."""
    ctx = SessionContext(
        platform="milky",
        chat_id="10001",
        session_id=PRIVATE_CHAT,
        chat_address=None,
    )
    req = memory_read_request_from_context(ctx, PRIVATE_CHAT)
    assert req.target_type == "private"
    assert req.chat_scope_id == PRIVATE_CHAT


def test_context_request_legacy_session_is_global_only() -> None:
    ctx = SessionContext(
        platform="milky",
        chat_id="10001",
        session_id="milky:10001",  # legacy / untyped
        chat_address=None,
    )
    req = memory_read_request_from_context(ctx, "milky:10001")
    assert req.target_type == "unknown"
    assert req.chat_scope_id == ""
    assert resolve_memory_read_scopes(req) == [GLOBAL]


def test_context_request_group_keeps_sender_memory_private_by_default() -> None:
    req = memory_read_request_from_context(
        _ctx(
            target_type="group",
            target_id="20001",
            session_id=GROUP_CHAT,
            person_id="owner",
            account_key=ACCOUNT,
        ),
        GROUP_CHAT,
    )
    # Linked person/account are known, but the group cascade still excludes them.
    assert req.person_id == "owner"
    assert req.sender_account_key == ACCOUNT
    assert resolve_memory_read_scopes(req) == [("chat", GROUP_CHAT), GLOBAL]


# ── resolve_memory_write_scope ──────────────────────────────


def test_write_global_kind_always_global() -> None:
    req = MemoryWriteRequest(
        chat_scope_id=PRIVATE_CHAT, person_id="owner", sender_account_key=ACCOUNT
    )
    for kind in ("decision", "procedure", "warning", "summary"):
        assert resolve_memory_write_scope(req, kind) == GLOBAL


def test_write_personal_linked_goes_to_person() -> None:
    req = MemoryWriteRequest(chat_scope_id=PRIVATE_CHAT, person_id="owner")
    for kind in ("preference", "fact", "task"):
        assert resolve_memory_write_scope(req, kind) == ("person", "owner")


def test_write_personal_unlinked_goes_to_account() -> None:
    req = MemoryWriteRequest(chat_scope_id=PRIVATE_CHAT, sender_account_key=ACCOUNT)
    assert resolve_memory_write_scope(req, "preference") == ("account", ACCOUNT)


def test_write_personal_linked_takes_precedence_over_account() -> None:
    req = MemoryWriteRequest(
        chat_scope_id=PRIVATE_CHAT, person_id="owner", sender_account_key=ACCOUNT
    )
    assert resolve_memory_write_scope(req, "preference") == ("person", "owner")


def test_write_personal_identity_off_goes_to_chat() -> None:
    """No person, no account → V1 chat scope."""
    req = MemoryWriteRequest(chat_scope_id=PRIVATE_CHAT)
    assert resolve_memory_write_scope(req, "preference") == ("chat", PRIVATE_CHAT)


def test_write_personal_legacy_no_chat_goes_to_global() -> None:
    req = MemoryWriteRequest(chat_scope_id="")
    assert resolve_memory_write_scope(req, "preference") == GLOBAL


# ── memory_write_request_from_context ───────────────────────


def test_write_context_request_carries_identity_and_chat_scope() -> None:
    req = memory_write_request_from_context(
        _ctx(person_id="owner", account_key=ACCOUNT), PRIVATE_CHAT
    )
    assert req.chat_scope_id == PRIVATE_CHAT
    assert req.person_id == "owner"
    assert req.sender_account_key == ACCOUNT


def test_write_context_request_falls_back_to_session_id_without_address() -> None:
    ctx = SessionContext(
        platform="milky",
        chat_id="10001",
        session_id=PRIVATE_CHAT,
        chat_address=None,
    )
    req = memory_write_request_from_context(ctx, PRIVATE_CHAT)
    assert req.chat_scope_id == PRIVATE_CHAT
    assert req.person_id is None
    assert req.sender_account_key == ""


def test_write_context_request_legacy_session_has_no_chat_scope() -> None:
    ctx = SessionContext(
        platform="milky",
        chat_id="10001",
        session_id="milky:10001",
        chat_address=None,
    )
    req = memory_write_request_from_context(ctx, "milky:10001")
    assert req.chat_scope_id == ""
    # Identity-off + no chat scope → write resolves to global for any kind.
    assert resolve_memory_write_scope(req, "preference") == GLOBAL
