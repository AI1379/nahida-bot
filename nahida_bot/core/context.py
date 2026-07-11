"""Session-scoped context variables for cross-layer context propagation."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.plugins.base import InboundAttachment


@dataclass(slots=True, frozen=True)
class SessionContext:
    """Carries the current turn's identity and routing through the call stack.

    ``session_id`` remains the legacy history/run key.  The explicit routing
    fields below prevent consumers from having to infer transport, actor,
    conversation, reply destination, and credential from that one string.
    Channel-originated turns initially use compatibility values; node and
    other non-channel transports can supply independent values later.
    """

    platform: str  # e.g. "telegram"
    chat_id: str  # e.g. "12345"
    session_id: (
        str  # e.g. "telegram:private:12345" or "telegram:private:12345:abc12345"
    )
    workspace_id: str | None = None
    chat_address: ChatAddress | None = None
    user_id: str = ""
    sender_display_name: str = ""
    # Identity (issue #7). Empty/None when identity is disabled or the sender's
    # account could not be derived. Populated by MessageRouter via IdentityResolver.
    sender_account_key: str = ""
    person_id: str | None = None
    # Boundary fields introduced while finishing issue #7.  They deliberately
    # do not replace the legacy fields yet, so existing plugins and persisted
    # sessions remain compatible during the migration.
    transport_address: str = ""
    conversation_id: str = ""
    reply_route: str = ""
    credential_id: str = ""

    @property
    def effective_conversation_id(self) -> str:
        """Return the explicit conversation id or the legacy session id."""

        return self.conversation_id or self.session_id

    @property
    def actor_account_key(self) -> str:
        """Stable actor-account alias used by new identity-aware code."""

        return self.sender_account_key


@dataclass(slots=True, frozen=True)
class AgentRunContext:
    """Carries orchestration run identity through tool execution."""

    run_id: str
    session_id: str
    requester_session_id: str
    depth: int = 0
    task_id: str | None = None


# Set by MessageRouter before each agent run; read by tool handlers.
current_session: ContextVar[SessionContext | None] = ContextVar(
    "current_session", default=None
)

# Set by AgentOrchestrator for child runs. Main router-driven runs do not set
# this yet and are treated as depth=0 by orchestration tools.
current_agent_run: ContextVar[AgentRunContext | None] = ContextVar(
    "current_agent_run", default=None
)

# Set by SessionRunner during an agent run so built-in tool handlers can resolve
# media attached to the in-flight turn before that turn is persisted to memory.
current_attachments: ContextVar[tuple[InboundAttachment, ...]] = ContextVar(
    "current_attachments", default=()
)
