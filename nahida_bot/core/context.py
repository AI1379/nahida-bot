"""Session-scoped context variables for cross-layer context propagation."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

from nahida_bot.plugins.base import InboundAttachment


@dataclass(slots=True, frozen=True)
class SessionContext:
    """Carries the current request's session identity through the call stack."""

    platform: str  # e.g. "telegram"
    chat_id: str  # e.g. "12345"
    session_id: str  # e.g. "telegram:12345" or "telegram:12345:abc12345"
    workspace_id: str | None = None


# Set by MessageRouter before each agent run; read by tool handlers.
current_session: ContextVar[SessionContext | None] = ContextVar(
    "current_session", default=None
)

# Set by SessionRunner during an agent run so built-in tool handlers can resolve
# media attached to the in-flight turn before that turn is persisted to memory.
current_attachments: ContextVar[tuple[InboundAttachment, ...]] = ContextVar(
    "current_attachments", default=()
)
