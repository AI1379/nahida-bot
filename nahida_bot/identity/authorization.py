"""Action authorization gate (Phase A, issue #7).

The bot's hard trust boundary. Privileged tool calls — shell ``exec``,
cross-session ``message``, ``workspace_write``, management commands — are
permitted only when the sender's account is in the config-declared admin set.

Chat-domain scoping adds a second, orthogonal axis: read-only history tools
declare ``scope="chat_domain"`` and are additionally available to non-admin
senders when the target chat belongs to the same config-declared trust domain
as the current chat (main group + satellite groups). Cross-domain and
cross-private-chat access still requires an admin.

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

from collections.abc import Iterable, Mapping
from typing import Any


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
        "desktop_screenshot_capture",
        "desktop_screen_observe",
        "desktop_screenshot_send",
        "desktop_input",
    }
)

# Registry scope mode for tools whose visibility is bounded by chat trust
# domains instead of the binary admin gate: a non-admin sender may use them
# only against chats in the same declared domain as the current chat.
TOOL_SCOPE_CHAT_DOMAIN = "chat_domain"


def chat_key_from_session_id(session_id: str) -> str:
    """Strip a derived session id down to its chat key.

    Session ids look like ``milky:group:833325688:8d738f35`` — the first three
    colon segments are the chat address; anything after is per-session suffix.
    """
    if not session_id:
        return ""
    parts = session_id.split(":")
    if len(parts) >= 3:
        return ":".join(parts[:3])
    return session_id


class ChatDomainIndex:
    """Config-declared chat trust domains (chat-domain scoping).

    A domain is a named set of chat addresses — e.g. a main QQ group plus its
    satellite groups — that share read visibility for chat-domain-scoped
    tools. Chats not listed in any domain form their own singleton domain, so
    unconfigured deployments degrade to "current chat only" (fail-closed).
    Overlapping chat lists are a config error; the first domain wins.

    Pure config data: consulting the index elsewhere does not violate the
    "admin status only in this module" rule.
    """

    def __init__(self, domains: Mapping[str, Iterable[str]] | None = None) -> None:
        self._domains: dict[str, frozenset[str]] = {}
        self._chat_to_domain: dict[str, str] = {}
        for name, chats in (domains or {}).items():
            members = frozenset(chat for chat in chats if chat)
            if not members:
                continue
            self._domains[name] = members
            for chat in sorted(members):
                self._chat_to_domain.setdefault(chat, name)

    def domain_of(self, chat_address: str) -> str:
        """Domain name owning ``chat_address``, or "" for singleton chats."""
        return self._chat_to_domain.get(chat_address, "")

    def same_domain(self, chat_a: str, chat_b: str) -> bool:
        """True when both addresses are the same chat or share a domain."""
        if not chat_a or not chat_b:
            return False
        if chat_a == chat_b:
            return True
        domain_a = self.domain_of(chat_a)
        return bool(domain_a) and domain_a == self.domain_of(chat_b)

    def domain_chats(self, chat_address: str) -> frozenset[str]:
        """All chats visible from ``chat_address`` (its domain plus itself)."""
        domain = self.domain_of(chat_address)
        if not domain:
            return frozenset({chat_address}) if chat_address else frozenset()
        return self._domains[domain] | {chat_address}


class NotAuthorized(Exception):
    """A non-admin sender invoked a privileged tool."""

    def __init__(self, tool_name: str, sender_account_key: str) -> None:
        self.tool_name = tool_name
        self.sender_account_key = sender_account_key
        super().__init__(
            f"Tool '{tool_name}' requires admin authorization; sender "
            f"{sender_account_key or '(unknown)'} is not a declared admin."
        )


class NotInChatScope(Exception):
    """A scoped tool targeted a chat outside the sender's chat domain."""

    def __init__(
        self,
        tool_name: str,
        sender_account_key: str,
        target_chat: str,
        current_chat: str,
    ) -> None:
        self.tool_name = tool_name
        self.sender_account_key = sender_account_key
        self.target_chat = target_chat
        self.current_chat = current_chat
        super().__init__(
            f"Tool '{tool_name}' target chat '{target_chat}' is outside the "
            f"sender's chat-domain scope (current chat "
            f"'{current_chat or '(unknown)'}'); cross-domain history access "
            "requires an admin sender."
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
        domains: ChatDomainIndex | None = None,
    ) -> None:
        self._admins = frozenset(admin_account_keys or ())
        self._enabled = enabled
        self._domains = domains if domains is not None else ChatDomainIndex()

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

    def allowed_chats_for(self, chat_address: str) -> frozenset[str]:
        """Chats a non-admin sender in ``chat_address`` may read via scoped tools."""
        return self._domains.domain_chats(chat_address)

    def authorize(
        self,
        tool_name: str,
        sender_account_key: str,
        arguments: dict[str, Any] | None = None,
        *,
        requires_admin: bool = False,
        scope: str = "",
        chat_address: str = "",
    ) -> None:
        """Raise if this tool call is not allowed for the sender.

        Non-privileged tools always pass unless the tool registry marks them
        admin-only. A disabled gate passes everything (legacy behavior when
        identity is off). ``arguments`` is consulted only for chat-domain
        scoped tools, to validate the call's target chat.
        """
        if not self._enabled:
            return
        privileged = self.is_privileged(tool_name) or requires_admin
        if privileged:
            if self.is_admin(sender_account_key):
                return
            raise NotAuthorized(tool_name, sender_account_key)
        if scope == TOOL_SCOPE_CHAT_DOMAIN and not self.is_admin(sender_account_key):
            self._authorize_chat_scope(
                tool_name, sender_account_key, arguments or {}, chat_address
            )

    def _authorize_chat_scope(
        self,
        tool_name: str,
        sender_account_key: str,
        arguments: dict[str, Any],
        current_chat: str,
    ) -> None:
        """Allow scoped calls whose target chat shares the current chat's domain.

        No explicit target means "the current chat", which is always in scope.
        An explicit target is resolved from ``chat_address`` or a ``session_id``
        prefix and must be the current chat or a declared domain sibling.
        """
        target = str(arguments.get("chat_address") or "").strip()
        if not target:
            session_id = str(arguments.get("session_id") or "").strip()
            target = chat_key_from_session_id(session_id) if session_id else ""
        if not target or self._domains.same_domain(target, current_chat):
            return
        raise NotInChatScope(
            tool_name, sender_account_key, target_chat=target, current_chat=current_chat
        )
