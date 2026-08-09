"""Action authorization gate (Phase A, issue #7).

The bot's hard trust boundary. Privileged tool calls — shell ``exec``,
cross-session ``message``, ``workspace_write``, management commands — are
permitted only when the sender's account is in the config-declared admin set.

This module is the **sole** place admin status is consulted. It is deliberately
decoupled from memory: memory subsystem code
(``nahida_bot.agent.memory.*``, ``nahida_bot.identity.policy``,
``nahida_bot.agent.retrieval.*``) must never import or branch on it — the agent
loop calls it at the tool-dispatch boundary. See
``docs/design/memory-soft-scope-and-authz.md`` §4.4 and
``docs/design/person-identity-system.md`` §2.5.

Security posture:
- ``enabled=False`` (identity subsystem off, the default) → the gate is a
  no-op, preserving legacy behavior exactly.
- ``enabled=True`` → enforce, **fail-closed**: an empty admin set denies every
  privileged call. Turning identity on requires declaring admins; otherwise the
  owner notices immediately and fixes config (safe + loud, never silently open).
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping, TypedDict

# Tool names that require an admin sender. These have system-side or
# cross-session effects. ``memory_write`` is deliberately NOT here: it writes
# the sender's own memory scope and is a memory concern, not an authorization
# one (gating it would violate the auth/memory decoupling).
PRIVILEGED_TOOLS: frozenset[str] = frozenset(
    {
        "exec",
        "message",
        "workspace_write",
        "identity_manage",
        "desktop_exec",
        "desktop_file_read",
    }
)

# Identity administration can never be delegated.  A ticket may authorize one
# exact invocation of the remaining tools, bound to the requesting account.
DELEGABLE_TOOLS: frozenset[str] = PRIVILEGED_TOOLS - {"identity_manage"}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _argument_fingerprint(arguments: dict[str, Any]) -> str:
    payload = json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class AuthorizationChallenge:
    challenge_id: str
    requester_account_key: str
    tool_name: str
    argument_fingerprint: str
    arguments: dict[str, Any]
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class AuthorizationGrant:
    grant_id: str
    challenge_id: str
    requester_account_key: str
    tool_name: str
    argument_fingerprint: str
    approved_by: str
    created_at: datetime
    expires_at: datetime


class NotAuthorized(Exception):
    """A non-admin sender invoked a privileged tool."""

    def __init__(self, tool_name: str, sender_account_key: str) -> None:
        self.tool_name = tool_name
        self.sender_account_key = sender_account_key
        super().__init__(
            f"Tool '{tool_name}' requires admin authorization; sender "
            f"{sender_account_key or '(unknown)'} is not a declared admin."
        )


class TicketStatus(TypedDict):
    """Snapshot of outstanding challenges and grants returned by ticket_status."""

    challenges: list[AuthorizationChallenge]
    grants: list[AuthorizationGrant]


class AuthorizationGate:
    """Check ``sender_account_key ∈ declared admin set`` before privileged tools.

    Authorization keys on the platform-authenticated account (``AccountKey``),
    never on the memory ``Person`` — platforms already authenticate accounts, so
    a config-declared admin set is sufficient with no impersonation window
    (person-identity-system.md §2.5).
    """

    def __init__(
        self,
        admin_account_keys: frozenset[str] | None = None,
        *,
        enabled: bool = True,
        tickets_enabled: bool = False,
        challenge_ttl_seconds: int = 600,
        grant_ttl_seconds: int = 300,
        max_grant_ttl_seconds: int = 900,
    ) -> None:
        self._admins = frozenset(admin_account_keys or ())
        self._enabled = enabled
        self._tickets_enabled = tickets_enabled
        self._challenge_ttl_seconds = max(60, challenge_ttl_seconds)
        self._grant_ttl_seconds = max(30, grant_ttl_seconds)
        self._max_grant_ttl_seconds = max(
            self._grant_ttl_seconds, max_grant_ttl_seconds
        )
        self._challenges: dict[str, AuthorizationChallenge] = {}
        self._grants: dict[str, AuthorizationGrant] = {}

    @property
    def enabled(self) -> bool:
        """False ⇒ gate is a no-op (identity subsystem off)."""
        return self._enabled

    def is_admin(self, sender_account_key: str) -> bool:
        """True only for a non-empty account key present in the admin set."""
        return bool(sender_account_key) and sender_account_key in self._admins

    @property
    def tickets_enabled(self) -> bool:
        return self._enabled and self._tickets_enabled

    @staticmethod
    def is_privileged(tool_name: str) -> bool:
        return tool_name in PRIVILEGED_TOOLS

    def authorize(
        self,
        tool_name: str,
        sender_account_key: str,
        arguments: dict[str, Any] | None = None,
    ) -> None:
        """Raise :class:`NotAuthorized` if a privileged tool is called by a non-admin.

        Non-privileged tools always pass. A disabled gate passes everything
        (legacy behavior when identity is off).
        """
        if not self._enabled:
            return
        if not self.is_privileged(tool_name) or self.is_admin(sender_account_key):
            return
        if self._consume_matching_grant(tool_name, sender_account_key, arguments or {}):
            return
        raise NotAuthorized(tool_name, sender_account_key)

    def request_ticket(
        self,
        *,
        requester_account_key: str,
        tool_name: str,
        arguments: dict[str, Any],
        now: datetime | None = None,
    ) -> AuthorizationChallenge:
        """Create an admin-approval challenge for one exact tool invocation."""
        if not self.tickets_enabled:
            raise ValueError("authorization tickets are disabled")
        if not requester_account_key:
            raise ValueError("requester account identity is unavailable")
        if self.is_admin(requester_account_key):
            raise ValueError("declared admins do not need authorization tickets")
        if tool_name not in DELEGABLE_TOOLS:
            raise ValueError(f"tool {tool_name!r} cannot be delegated")
        if not isinstance(arguments, dict) or not arguments:
            raise ValueError("exact non-empty tool arguments are required")

        current = now or _utc_now()
        self._prune(current)
        challenge_id = self._new_id("CH", self._challenges)
        challenge = AuthorizationChallenge(
            challenge_id=challenge_id,
            requester_account_key=requester_account_key,
            tool_name=tool_name,
            argument_fingerprint=_argument_fingerprint(arguments),
            arguments=dict(arguments),
            created_at=current,
            expires_at=current + timedelta(seconds=self._challenge_ttl_seconds),
        )
        self._challenges[challenge_id] = challenge
        return challenge

    def approve_ticket(
        self,
        *,
        challenge_id: str,
        admin_account_key: str,
        ttl_seconds: int | None = None,
        now: datetime | None = None,
    ) -> AuthorizationGrant:
        """Approve a pending challenge as a one-use, account-bound grant."""
        if not self.tickets_enabled:
            raise ValueError("authorization tickets are disabled")
        if not self.is_admin(admin_account_key):
            raise NotAuthorized("authorization_ticket_approve", admin_account_key)

        current = now or _utc_now()
        self._prune(current)
        challenge = self._challenges.pop(challenge_id.upper(), None)
        if challenge is None:
            raise ValueError("authorization challenge not found or expired")
        requested_ttl = ttl_seconds or self._grant_ttl_seconds
        effective_ttl = min(max(30, requested_ttl), self._max_grant_ttl_seconds)
        grant_id = self._new_id("GR", self._grants)
        grant = AuthorizationGrant(
            grant_id=grant_id,
            challenge_id=challenge.challenge_id,
            requester_account_key=challenge.requester_account_key,
            tool_name=challenge.tool_name,
            argument_fingerprint=challenge.argument_fingerprint,
            approved_by=admin_account_key,
            created_at=current,
            expires_at=current + timedelta(seconds=effective_ttl),
        )
        self._grants[grant_id] = grant
        return grant

    def revoke_ticket(self, ticket_id: str, *, admin_account_key: str) -> bool:
        if not self.is_admin(admin_account_key):
            raise NotAuthorized("authorization_ticket_revoke", admin_account_key)
        normalized = ticket_id.upper()
        return (
            self._challenges.pop(normalized, None) is not None
            or self._grants.pop(normalized, None) is not None
        )

    def ticket_status(
        self, *, actor_account_key: str, now: datetime | None = None
    ) -> TicketStatus:
        current = now or _utc_now()
        self._prune(current)
        is_admin = self.is_admin(actor_account_key)
        challenges = [
            item
            for item in self._challenges.values()
            if is_admin or item.requester_account_key == actor_account_key
        ]
        grants = [
            item
            for item in self._grants.values()
            if is_admin or item.requester_account_key == actor_account_key
        ]
        return {"challenges": challenges, "grants": grants}

    def _consume_matching_grant(
        self,
        tool_name: str,
        sender_account_key: str,
        arguments: dict[str, Any],
    ) -> bool:
        if not self.tickets_enabled or tool_name not in DELEGABLE_TOOLS:
            return False
        self._prune(_utc_now())
        fingerprint = _argument_fingerprint(arguments)
        for grant_id, grant in tuple(self._grants.items()):
            if (
                grant.requester_account_key == sender_account_key
                and grant.tool_name == tool_name
                and secrets.compare_digest(grant.argument_fingerprint, fingerprint)
            ):
                # Pop before execution: even a failed tool consumes the grant,
                # preventing retries from turning one approval into many actions.
                self._grants.pop(grant_id, None)
                return True
        return False

    def _prune(self, now: datetime) -> None:
        self._challenges = {
            key: item for key, item in self._challenges.items() if item.expires_at > now
        }
        self._grants = {
            key: item for key, item in self._grants.items() if item.expires_at > now
        }

    @staticmethod
    def _new_id(prefix: str, existing: Mapping[str, object]) -> str:
        while True:
            value = f"{prefix}-{secrets.token_hex(4).upper()}"
            if value not in existing:
                return value
