"""Canonical agent-run runtime (agent-loop repair).

Phase 1 ships the canonical run ledger: domain models, a storage interface,
and the :class:`RunRecorder` that the agent loop drives. Later phases add
verification (Phase 2/3) and transcript projection/replay (Phase 5) here.
"""

from nahida_bot.agent.runtime.models import (
    AgentRunContext,
    ExecutionReceipt,
    RunEvent,
    TerminalState,
)
from nahida_bot.agent.runtime.recorder import RunRecorder
from nahida_bot.agent.runtime.store import (
    AgentRunClosedError,
    AgentRunStore,
    NullAgentRunStore,
)

__all__ = [
    "AgentRunClosedError",
    "AgentRunContext",
    "AgentRunStore",
    "ExecutionReceipt",
    "NullAgentRunStore",
    "RunEvent",
    "RunRecorder",
    "TerminalState",
]
