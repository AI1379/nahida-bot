"""Tests for the Phase A action-authorization gate (issue #7).

Covers gate semantics (privileged set, admin check, fail-closed, disabled
no-op) and the auth/memory decoupling invariant: memory-subsystem code must not
import or branch on ``identity.authorization`` (person-identity-system.md §2.5,
docs/design/memory-soft-scope-and-authz.md §4.4).
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime, timedelta

import pytest

from nahida_bot.identity.authorization import (
    DELEGABLE_TOOLS,
    PRIVILEGED_TOOLS,
    AuthorizationGate,
    NotAuthorized,
)


# --- gate semantics ---------------------------------------------------------


def test_privileged_set_covers_system_tools_not_memory_write() -> None:
    assert "exec" in PRIVILEGED_TOOLS
    assert "message" in PRIVILEGED_TOOLS
    assert "workspace_write" in PRIVILEGED_TOOLS
    assert "desktop_exec" in PRIVILEGED_TOOLS
    assert "desktop_file_read" in PRIVILEGED_TOOLS
    assert "desktop_exec" in DELEGABLE_TOOLS
    assert "desktop_file_read" in DELEGABLE_TOOLS
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


# --- one-use delegated authorization ---------------------------------------


def _ticket_gate() -> AuthorizationGate:
    return AuthorizationGate(
        frozenset({"milky:user:admin"}),
        enabled=True,
        tickets_enabled=True,
        challenge_ttl_seconds=600,
        grant_ttl_seconds=300,
    )


def test_admin_can_approve_one_exact_tool_invocation() -> None:
    gate = _ticket_gate()
    arguments = {"command": "git status --short", "workdir": "."}
    challenge = gate.request_ticket(
        requester_account_key="milky:user:guest",
        tool_name="exec",
        arguments=arguments,
    )
    grant = gate.approve_ticket(
        challenge_id=challenge.challenge_id,
        admin_account_key="milky:user:admin",
    )

    assert grant.requester_account_key == "milky:user:guest"
    gate.authorize("exec", "milky:user:guest", arguments)
    with pytest.raises(NotAuthorized):
        gate.authorize("exec", "milky:user:guest", arguments)


def test_ticket_rejects_wrong_account_or_arguments_without_consuming() -> None:
    gate = _ticket_gate()
    approved = {"path": "approved.txt", "content": "ok"}
    challenge = gate.request_ticket(
        requester_account_key="milky:user:guest",
        tool_name="workspace_write",
        arguments=approved,
    )
    gate.approve_ticket(
        challenge_id=challenge.challenge_id,
        admin_account_key="milky:user:admin",
    )

    with pytest.raises(NotAuthorized):
        gate.authorize("workspace_write", "milky:user:other", approved)
    with pytest.raises(NotAuthorized):
        gate.authorize(
            "workspace_write",
            "milky:user:guest",
            {"path": "approved.txt", "content": "changed"},
        )
    gate.authorize("workspace_write", "milky:user:guest", approved)


def test_identity_management_cannot_be_delegated() -> None:
    gate = _ticket_gate()
    with pytest.raises(ValueError, match="cannot be delegated"):
        gate.request_ticket(
            requester_account_key="milky:user:guest",
            tool_name="identity_manage",
            arguments={"action": "link"},
        )


def test_only_declared_admin_can_approve_or_revoke() -> None:
    gate = _ticket_gate()
    challenge = gate.request_ticket(
        requester_account_key="milky:user:guest",
        tool_name="exec",
        arguments={"command": "pwd"},
    )
    with pytest.raises(NotAuthorized):
        gate.approve_ticket(
            challenge_id=challenge.challenge_id,
            admin_account_key="milky:user:other",
        )
    with pytest.raises(NotAuthorized):
        gate.revoke_ticket(
            challenge.challenge_id,
            admin_account_key="milky:user:other",
        )
    assert gate.revoke_ticket(
        challenge.challenge_id,
        admin_account_key="milky:user:admin",
    )


def test_expired_challenge_cannot_be_approved() -> None:
    gate = _ticket_gate()
    now = datetime(2026, 7, 13, tzinfo=UTC)
    challenge = gate.request_ticket(
        requester_account_key="milky:user:guest",
        tool_name="exec",
        arguments={"command": "pwd"},
        now=now,
    )
    with pytest.raises(ValueError, match="not found or expired"):
        gate.approve_ticket(
            challenge_id=challenge.challenge_id,
            admin_account_key="milky:user:admin",
            now=now + timedelta(minutes=11),
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
