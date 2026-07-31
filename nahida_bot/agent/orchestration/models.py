"""Models for local agent/subagent orchestration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from nahida_bot.core.chat_address import ChatAddress


def utc_now() -> datetime:
    """Return an aware UTC timestamp."""
    return datetime.now(UTC)


class AgentRunStatus(StrEnum):
    """Lifecycle states shared by runs and background tasks."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    LOST = "lost"


class AgentRunKind(StrEnum):
    """Kinds of agent execution."""

    MAIN = "main"
    SUBAGENT = "subagent"
    CRON = "cron"
    CLI = "cli"


class TaskRuntime(StrEnum):
    """Persistent background task runtime kinds."""

    SUBAGENT = "subagent"
    CRON = "cron"
    CLI = "cli"
    REMOTE_NODE = "remote_node"


@dataclass(slots=True, frozen=True)
class SubagentSpec:
    """One-off task specification supplied by the parent agent."""

    task: str
    label: str | None = None
    instructions: str | None = None
    context_mode: Literal["isolated", "summary", "fork"] = "isolated"
    handoff_summary: str | None = None
    provider_id: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    timeout_seconds: int | None = None
    tool_allowlist: tuple[str, ...] = ()
    tool_denylist: tuple[str, ...] = ()
    notify_policy: Literal["done_only", "silent"] = "done_only"


@dataclass(slots=True)
class AgentRun:
    """In-memory record for one concrete agent execution."""

    run_id: str
    kind: AgentRunKind
    session_id: str
    parent_run_id: str | None
    requester_session_id: str | None
    task_id: str | None
    status: AgentRunStatus = AgentRunStatus.QUEUED
    depth: int = 0
    asyncio_task: asyncio.Task[Any] | None = None
    created_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    summary: str = ""
    error: str = ""


@dataclass(slots=True, frozen=True)
class BackgroundTask:
    """Persistent task ledger entry."""

    # TODO(subagent-observability): Add a durable stream/event model for live
    # subagent progress, partial output, tool calls, and final result inspection.
    task_id: str
    runtime: TaskRuntime
    status: AgentRunStatus
    requester_session_id: str
    child_session_id: str | None
    parent_task_id: str | None
    title: str
    summary: str = ""
    delivery_target: dict[str, str] | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    ended_at: datetime | None = None
    error: str = ""
    # Trusted terminal state propagated from the agent loop (issue #42). One
    # of ``completed`` / ``incomplete`` / ``failed`` / ``cancelled``. Empty
    # until the run reaches a terminal state.
    terminal_state: str = ""
    terminal_reason: str = ""
    # Idempotency marker for delivery (issue #41). Empty until the completion
    # notification has been dispatched to the delivery channel.
    delivery_claimed_at: datetime | None = None
    delivered_at: datetime | None = None


@dataclass(slots=True, frozen=True)
class AgentRunPayload:
    """Payload consumed by an AgentRunExecutor."""

    user_message: str
    system_prompt: str
    requester_session_id: str
    workspace_id: str | None = None
    provider_id: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    # ``None`` means the parent did not request an allowlist restriction.
    # An explicit empty set means the parent requested a restrictive
    # allowlist but every requested tool was denied by policy, so the child
    # must receive no tools rather than falling back to an unrestricted set.
    tool_allowlist: frozenset[str] | None = None
    tool_filter: frozenset[str] = frozenset()
    timeout_seconds: int | None = None
    # Identity delegation (issue #39): the child run inherits the *auditable*
    # actor account key of the parent's sender. This does NOT grant new
    # capability on its own — the child still has to clear the
    # ``AuthorizationGate`` for privileged tools. Empty when the parent's
    # sender identity was unresolved (identity subsystem off or unknown
    # account): privileged tools then fail-closed inside the child.
    sender_account_key: str = ""
    # The original chat address so completion delivery and reply routing can
    # target the channel the user actually spoke to, not the synthetic child
    # ``platform=agent`` address (issue #41).
    chat_address: ChatAddress | None = None
