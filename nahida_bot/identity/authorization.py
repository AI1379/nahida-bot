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

# Tool names that require an admin sender. These have system-side or
# cross-session effects. ``memory_write`` is deliberately NOT here: it writes
# the sender's own memory scope and is a memory concern, not an authorization
# one (gating it would violate the auth/memory decoupling).
PRIVILEGED_TOOLS: frozenset[str] = frozenset({"exec", "message", "workspace_write"})


class NotAuthorized(Exception):
    """A non-admin sender invoked a privileged tool."""

    def __init__(self, tool_name: str, sender_account_key: str) -> None:
        self.tool_name = tool_name
        self.sender_account_key = sender_account_key
        super().__init__(
            f"Tool '{tool_name}' requires admin authorization; sender "
            f"{sender_account_key or '(unknown)'} is not a declared admin."
        )


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
    ) -> None:
        self._admins = frozenset(admin_account_keys or ())
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        """False ⇒ gate is a no-op (identity subsystem off)."""
        return self._enabled

    def is_admin(self, sender_account_key: str) -> bool:
        """True only for a non-empty account key present in the admin set."""
        return bool(sender_account_key) and sender_account_key in self._admins

    @staticmethod
    def is_privileged(tool_name: str) -> bool:
        return tool_name in PRIVILEGED_TOOLS

    def authorize(self, tool_name: str, sender_account_key: str) -> None:
        """Raise :class:`NotAuthorized` if a privileged tool is called by a non-admin.

        Non-privileged tools always pass. A disabled gate passes everything
        (legacy behavior when identity is off).
        """
        if not self._enabled:
            return
        if self.is_privileged(tool_name) and not self.is_admin(sender_account_key):
            raise NotAuthorized(tool_name, sender_account_key)
