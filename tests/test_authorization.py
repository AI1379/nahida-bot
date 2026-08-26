"""Tests for the Phase A action-authorization gate (issue #7).

Covers gate semantics (privileged set, admin check, fail-closed, disabled
no-op), chat-domain scoping (trust domains, scoped tool authorization, allowed
chat resolution), and the auth/memory decoupling invariant: memory-subsystem
code must not import or branch on ``identity.authorization``
(person-identity-system.md §2.5, docs/design/memory-soft-scope-and-authz.md §4.4).
"""

from __future__ import annotations

import pathlib

import pytest

from nahida_bot.identity.authorization import (
    PRIVILEGED_TOOLS,
    TOOL_SCOPE_CHAT_DOMAIN,
    AuthorizationGate,
    ChatDomainIndex,
    NotAuthorized,
    NotInChatScope,
    chat_key_from_session_id,
)


# --- gate semantics ---------------------------------------------------------


def test_privileged_set_covers_system_tools_not_memory_write() -> None:
    assert "exec" in PRIVILEGED_TOOLS
    assert "message" in PRIVILEGED_TOOLS
    assert "workspace_write" in PRIVILEGED_TOOLS
    assert "desktop_exec" in PRIVILEGED_TOOLS
    assert "desktop_file_read" in PRIVILEGED_TOOLS
    assert "desktop_screenshot_capture" in PRIVILEGED_TOOLS
    assert "desktop_screen_observe" in PRIVILEGED_TOOLS
    assert "desktop_screenshot_send" in PRIVILEGED_TOOLS
    assert "desktop_input" in PRIVILEGED_TOOLS
    # memory_write is memory-side (writes own scope); gating it would couple
    # authorization into memory, violating §2.5.
    assert "memory_write" not in PRIVILEGED_TOOLS


def test_admin_passes_privileged_tool() -> None:
    gate = AuthorizationGate(frozenset({"milky:1"}), enabled=True)
    assert gate.is_admin("milky:1")
    gate.authorize("exec", "milky:1")  # no raise


def test_non_admin_denied_privileged_tool() -> None:
    gate = AuthorizationGate(frozenset({"milky:1"}), enabled=True)
    with pytest.raises(NotAuthorized) as exc:
        gate.authorize("exec", "milky:2")
    assert exc.value.tool_name == "exec"
    assert exc.value.sender_account_key == "milky:2"


def test_non_privileged_tools_pass_for_anyone() -> None:
    gate = AuthorizationGate(frozenset({"milky:1"}), enabled=True)
    gate.authorize("workspace_read", "milky:999")
    gate.authorize("memory_write", "milky:999")


def test_registration_metadata_can_require_admin_for_new_tool() -> None:
    gate = AuthorizationGate(frozenset({"milky:1"}), enabled=True)

    with pytest.raises(NotAuthorized):
        gate.authorize(
            "plugin_admin_action",
            "milky:2",
            requires_admin=True,
        )

    gate.authorize(
        "plugin_admin_action",
        "milky:1",
        requires_admin=True,
    )


def test_registration_metadata_cannot_downgrade_legacy_privileged_tool() -> None:
    gate = AuthorizationGate(frozenset({"milky:1"}), enabled=True)

    with pytest.raises(NotAuthorized):
        gate.authorize("exec", "milky:2", requires_admin=False)


def test_empty_sender_denied_when_enabled() -> None:
    gate = AuthorizationGate(frozenset({"milky:1"}), enabled=True)
    with pytest.raises(NotAuthorized):
        gate.authorize("exec", "")


def test_disabled_gate_is_noop() -> None:
    # identity subsystem off → legacy behavior, no gating.
    gate = AuthorizationGate(frozenset(), enabled=False)
    assert not gate.enabled
    gate.authorize("exec", "")  # no raise


def test_enabled_with_empty_admins_is_fail_closed() -> None:
    # Turning identity on without declaring admins must lock privileged tools,
    # not silently open them.
    gate = AuthorizationGate(frozenset(), enabled=True)
    with pytest.raises(NotAuthorized):
        gate.authorize("exec", "milky:1")


# --- chat-domain scoping ------------------------------------------------------


def _domain_gate() -> AuthorizationGate:
    return AuthorizationGate(
        frozenset({"milky:admin"}),
        enabled=True,
        domains=ChatDomainIndex(
            {
                "main": ["milky:group:100", "milky:group:200"],
                "other": ["milky:group:300"],
            }
        ),
    )


def test_chat_key_from_session_id_strips_suffix() -> None:
    assert (
        chat_key_from_session_id("milky:group:833325688:8d738f35")
        == "milky:group:833325688"
    )
    assert chat_key_from_session_id("milky:group:100") == "milky:group:100"
    assert chat_key_from_session_id("") == ""


def test_domain_index_same_domain_semantics() -> None:
    index = ChatDomainIndex({"main": ["milky:group:100", "milky:group:200"]})
    # Same chat, domain siblings, unrelated chats, empty values.
    assert index.same_domain("milky:group:100", "milky:group:100")
    assert index.same_domain("milky:group:100", "milky:group:200")
    assert not index.same_domain("milky:group:100", "milky:group:300")
    assert not index.same_domain("milky:group:100", "")
    assert not index.same_domain("", "milky:group:100")


def test_domain_index_unlisted_chat_is_singleton() -> None:
    index = ChatDomainIndex({"main": ["milky:group:100"]})
    assert index.domain_of("milky:group:555") == ""
    assert index.domain_chats("milky:group:555") == frozenset({"milky:group:555"})
    assert index.domain_chats("") == frozenset()


def test_domain_index_includes_self_and_siblings() -> None:
    index = ChatDomainIndex({"main": ["milky:group:100", "milky:group:200"]})
    assert index.domain_chats("milky:group:100") == frozenset(
        {"milky:group:100", "milky:group:200"}
    )


def test_domain_index_overlap_first_domain_wins() -> None:
    index = ChatDomainIndex(
        {"a": ["milky:group:1", "milky:group:2"], "b": ["milky:group:2"]}
    )
    assert index.domain_of("milky:group:2") == "a"


def test_scoped_tool_allows_domain_sibling_for_non_admin() -> None:
    gate = _domain_gate()
    gate.authorize(
        "read_chat_history",
        "milky:user",
        {"chat_address": "milky:group:200"},
        scope=TOOL_SCOPE_CHAT_DOMAIN,
        chat_address="milky:group:100",
    )


def test_scoped_tool_current_chat_target_implicit() -> None:
    gate = _domain_gate()
    # No explicit target ⇒ "current chat", always in scope.
    gate.authorize(
        "read_chat_history",
        "milky:user",
        {},
        scope=TOOL_SCOPE_CHAT_DOMAIN,
        chat_address="milky:group:100",
    )


def test_scoped_tool_resolves_target_from_session_id() -> None:
    gate = _domain_gate()
    gate.authorize(
        "read_chat_history",
        "milky:user",
        {"session_id": "milky:group:200:8d738f35"},
        scope=TOOL_SCOPE_CHAT_DOMAIN,
        chat_address="milky:group:100",
    )


def test_scoped_tool_denies_cross_domain_for_non_admin() -> None:
    gate = _domain_gate()
    with pytest.raises(NotInChatScope) as exc:
        gate.authorize(
            "read_chat_history",
            "milky:user",
            {"chat_address": "milky:group:300"},
            scope=TOOL_SCOPE_CHAT_DOMAIN,
            chat_address="milky:group:100",
        )
    assert exc.value.target_chat == "milky:group:300"
    assert exc.value.current_chat == "milky:group:100"


def test_scoped_tool_denies_other_unlisted_chat_for_non_admin() -> None:
    gate = _domain_gate()
    with pytest.raises(NotInChatScope):
        gate.authorize(
            "search_chat_history",
            "milky:user",
            {"chat_address": "milky:group:555"},
            scope=TOOL_SCOPE_CHAT_DOMAIN,
            chat_address="milky:group:100",
        )


def test_scoped_tool_admin_bypasses_domain_check() -> None:
    gate = _domain_gate()
    gate.authorize(
        "read_chat_history",
        "milky:admin",
        {"chat_address": "milky:group:300"},
        scope=TOOL_SCOPE_CHAT_DOMAIN,
        chat_address="milky:group:100",
    )


def test_scoped_tool_empty_current_chat_fails_closed_on_explicit_target() -> None:
    gate = _domain_gate()
    with pytest.raises(NotInChatScope):
        gate.authorize(
            "read_chat_history",
            "milky:user",
            {"chat_address": "milky:group:100"},
            scope=TOOL_SCOPE_CHAT_DOMAIN,
            chat_address="",
        )


def test_scoped_tool_disabled_gate_is_noop() -> None:
    gate = AuthorizationGate(
        frozenset(),
        enabled=False,
        domains=ChatDomainIndex({"main": ["milky:group:100"]}),
    )
    gate.authorize(
        "read_chat_history",
        "milky:user",
        {"chat_address": "milky:group:999"},
        scope=TOOL_SCOPE_CHAT_DOMAIN,
        chat_address="milky:group:100",
    )


def test_allowed_chats_for_resolves_domain() -> None:
    gate = _domain_gate()
    assert gate.allowed_chats_for("milky:group:100") == frozenset(
        {"milky:group:100", "milky:group:200"}
    )
    assert gate.allowed_chats_for("milky:group:555") == frozenset({"milky:group:555"})
    assert gate.allowed_chats_for("") == frozenset()


def test_privileged_check_takes_precedence_over_scope() -> None:
    # A tool that is both privileged and scoped is gated by the admin rule.
    gate = _domain_gate()
    with pytest.raises(NotAuthorized):
        gate.authorize(
            "exec",
            "milky:user",
            {"chat_address": "milky:group:200"},
            scope=TOOL_SCOPE_CHAT_DOMAIN,
            chat_address="milky:group:100",
        )


# --- auth/memory decoupling invariant ---------------------------------------


def test_memory_subsystem_does_not_depend_on_authorization() -> None:
    """Memory modules must not import or reference identity.authorization.

    Static source guard. The agent loop importing it is fine; memory code is
    not. If this fails, authorization logic has leaked into memory/scope/
    retrieval and the §2.5 decoupling is broken.
    """
    root = pathlib.Path("nahida_bot")
    forbidden = ("identity.authorization", "AuthorizationGate", "NotAuthorized")
    scan: list[pathlib.Path] = []
    for sub in ("agent/memory", "agent/retrieval"):
        scan.extend((root / sub).rglob("*.py"))
    scan.append(root / "identity" / "policy.py")

    offenders: list[str] = []
    for path in scan:
        if not path.exists():
            continue
        source = path.read_text(encoding="utf-8")
        for needle in forbidden:
            if needle in source:
                offenders.append(f"{path.relative_to(root)} references '{needle}'")

    assert not offenders, (
        "memory subsystem must not depend on identity.authorization "
        "(§2.5 decoupling):\n  " + "\n  ".join(offenders)
    )
