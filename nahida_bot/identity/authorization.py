"""Action authorization with standard, reviewed-relaxed and unsafe modes.

Identity resolution is independent of this policy. Standard mode requires an
admin for privileged tools. Relaxed mode reviews concrete non-admin execution;
unsafe mode permits it without review. Neither mode supplies OS isolation.

Chat-domain scoping adds a second, orthogonal axis: read-only history tools
declare ``scope="chat_domain"`` and are additionally available to non-admin
senders when the target chat belongs to the same config-declared trust domain
as the current chat (main group + satellite groups). Cross-domain and
cross-private-chat access still requires an admin.

This module centralizes action policy. It is deliberately
decoupled from memory: memory subsystem code
(``nahida_bot.agent.memory.*``, ``nahida_bot.identity.policy``,
``nahida_bot.agent.retrieval.*``) must never import or branch on it — the agent
loop calls it at the tool-dispatch boundary. See
``docs/design/memory-soft-scope-and-authz.md`` §4.4 and
``docs/design/person-identity-system.md`` §2.5.

The enabled=False constructor remains for embedded/legacy callers and tests;
the application always enables the gate, regardless of identity.enabled.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

import structlog

from nahida_bot.core.authorization_config import AuthorizationConfig, AuthorizationMode

if TYPE_CHECKING:
    from nahida_bot.identity.risk_review import RiskReviewer

from nahida_bot_sdk.chat_address import chat_key_from_session_id


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

# These direct control-plane and cross-chat operations never inherit an
# execution-mode override. Host shell access can still bypass OS-level
# protections: relaxed/unsafe are explicitly not security sandboxes.
ADMIN_ONLY_TOOLS = (PRIVILEGED_TOOLS - {"exec", "workspace_write"}) | {
    "mcp_add_server",
    "mcp_remove_server",
    "mcp_reload_server",
}
_REVIEW_EXEMPT_TOOLS = frozenset(
    {
        "workspace_read",
        "search_files",
        "web_fetch",
        "send_local_attachment",
        "memory_read",
        "memory_write",
        "memory_update",
        "memory_archive",
        "read_chat_history",
        "search_chat_history",
        "find_chat",
        "recall_cross_chat",
        "plan",
        "cron_list",
        "cron_cancel",
        "cron_delete",
        "cron_create",
        "cron_update",
        "agent_spawn",
        "agent_wait",
        "agent_yield",
        "agent_stop",
    }
)
_logger = structlog.get_logger(__name__)


class ActionDenied(Exception):
    """A policy/reviewer denial distinct from missing administrator status."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class _ReviewRequired(Exception):
    pass


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
        policy: AuthorizationConfig | None = None,
        reviewer: RiskReviewer | None = None,
    ) -> None:
        self._admins = frozenset(admin_account_keys or ())
        self._enabled = enabled
        self._domains = domains if domains is not None else ChatDomainIndex()
        self.policy = policy or AuthorizationConfig()
        self.reviewer = reviewer

    def mode_for(
        self, sender_account_key: str, chat_address: str = ""
    ) -> AuthorizationMode:
        """Use trusted account/chat context only, never tool arguments."""
        return self.policy.accounts.get(
            sender_account_key, self.policy.chats.get(chat_address, self.policy.mode)
        )

    def tool_guidance(self, sender_account_key: str, chat_address: str = "") -> str:
        """Describe the actual runtime policy so the model does not self-deny."""
        if self.is_admin(sender_account_key):
            return "Tool authorization: this authenticated account is a declared admin."
        mode = self.mode_for(sender_account_key, chat_address)
        common = (
            " Prefer search_files for configured reference directories and web_fetch "
            "for public web pages. These do not require administrator permission. "
            "Public search/image lookup is not forbidden merely because it uses "
            "the network. Use available tools; do not claim an admin requirement "
            "unless the tool actually reports it. Respect tool failures and scopes."
        )
        if mode == "standard":
            return (
                "Tool authorization: standard. exec/workspace_write require an admin."
                + common
            )
        return (
            f"Tool authorization: {mode}. This account may use exec and "
            "workspace_write without becoming an admin. Ordinary scripts, grep, "
            "web/image lookup and file processing are permitted. "
            + (
                "Concrete calls undergo independent risk review. "
                if mode == "relaxed"
                else "Calls are logged without risk review. "
            )
            + "Identity/server management, desktop control and cross-chat delivery "
            "still require admin authorization. Execution is on the host, not a sandbox."
            + common
        )

    @property
    def enabled(self) -> bool:
        """False only for explicitly disabled embedded/legacy gate instances."""
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

        Apply synchronous boundaries. Relaxed calls needing review raise an
        internal signal; production dispatch must use authorize_call instead.
        A disabled embedded gate passes everything. Arguments cannot select
        the account, mode or originating chat.
        """
        if not self._enabled:
            return
        if self.is_admin(sender_account_key):
            return
        if tool_name in ADMIN_ONLY_TOOLS:
            raise NotAuthorized(tool_name, sender_account_key)
        privileged = self.is_privileged(tool_name) or requires_admin
        mode = self.mode_for(sender_account_key, chat_address)
        if privileged and (not sender_account_key or mode == "standard"):
            raise NotAuthorized(tool_name, sender_account_key)
        if scope == TOOL_SCOPE_CHAT_DOMAIN:
            self._authorize_chat_scope(
                tool_name, sender_account_key, arguments or {}, chat_address
            )
        if (
            mode != "standard"
            and not sender_account_key
            and tool_name not in _REVIEW_EXEMPT_TOOLS
        ):
            raise NotAuthorized(tool_name, sender_account_key)
        if mode == "relaxed" and (privileged or tool_name not in _REVIEW_EXEMPT_TOOLS):
            if not sender_account_key:
                raise NotAuthorized(tool_name, sender_account_key)
            # Synchronous callers cannot silently skip the asynchronous review.
            raise _ReviewRequired()
        if mode == "unsafe":
            _logger.warning(
                "authorization.unsafe_allowed",
                tool_name=tool_name,
                sender_account_key=sender_account_key,
                chat_address=chat_address,
            )

    async def authorize_call(
        self,
        tool_name: str,
        sender_account_key: str,
        arguments: dict[str, Any] | None = None,
        *,
        requires_admin: bool = False,
        scope: str = "",
        chat_address: str = "",
        user_request: str = "",
        tool_description: str = "",
    ) -> None:
        """Authorize and, in relaxed mode, review the exact impending call."""
        try:
            self.authorize(
                tool_name,
                sender_account_key,
                arguments,
                requires_admin=requires_admin,
                scope=scope,
                chat_address=chat_address,
            )
            return
        except _ReviewRequired:
            pass
        from nahida_bot.identity.risk_review import (
            RiskReviewRequest,
            RiskReviewUnavailable,
        )

        if self.reviewer is None:
            raise ActionDenied(
                "risk_review_unavailable",
                "Risk-review model is not configured; no action was executed.",
            )
        try:
            verdict = await self.reviewer.review(
                RiskReviewRequest(
                    tool_name=tool_name,
                    arguments=arguments or {},
                    user_request=user_request,
                    tool_description=tool_description,
                    chat_address=chat_address,
                )
            )
        except RiskReviewUnavailable as exc:
            _logger.warning("authorization.review_unavailable", tool_name=tool_name)
            raise ActionDenied("risk_review_unavailable", str(exc)) from exc
        _logger.info(
            "authorization.reviewed",
            tool_name=tool_name,
            verdict=verdict.verdict,
            sender_account_key=sender_account_key,
            chat_address=chat_address,
        )
        if verdict.verdict == "deny":
            raise ActionDenied(
                "dangerous_action",
                f"Risk review blocked this action: {verdict.reason} Evidence: {verdict.evidence}",
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
