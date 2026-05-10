"""Models for local agent/subagent orchestration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal


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


@dataclass(slots=True, frozen=True)
class AgentRunPayload:
    """Payload consumed by an AgentRunExecutor."""

    user_message: str
    system_prompt: str
    requester_session_id: str
    workspace_id: str | None = None
    model: str | None = None
    tool_filter: frozenset[str] = frozenset()
    timeout_seconds: int | None = None
