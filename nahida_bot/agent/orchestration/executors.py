"""Agent run executors for local orchestration."""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from nahida_bot.agent.loop import AgentRunResult
from nahida_bot.agent.orchestration.models import AgentRun, AgentRunPayload
from nahida_bot.core.context import (
    AgentRunContext,
    SessionContext,
    current_agent_run,
    current_session,
)


class AgentRunExecutor(Protocol):
    """Executor interface that can later be backed by Gateway-Node."""

    async def run(self, run: AgentRun, payload: AgentRunPayload) -> AgentRunResult: ...


class LocalAgentRunExecutor:
    """Execute an AgentRun in the current process through SessionRunner."""

    def __init__(self, runner: Any) -> None:
        self._runner = runner

    async def run(self, run: AgentRun, payload: AgentRunPayload) -> AgentRunResult:
        session_token = current_session.set(
            SessionContext(
                platform="agent",
                chat_id=run.task_id or run.run_id,
                session_id=run.session_id,
                workspace_id=payload.workspace_id,
            )
        )
        run_token = current_agent_run.set(
            AgentRunContext(
                run_id=run.run_id,
                session_id=run.session_id,
                requester_session_id=payload.requester_session_id,
                depth=run.depth,
                task_id=run.task_id,
            )
        )
        try:
            coro = self._runner.run(
                user_message=payload.user_message,
                session_id=run.session_id,
                system_prompt=payload.system_prompt,
                workspace_id=payload.workspace_id,
                tool_filter=payload.tool_filter,
                source_tag="subagent_task",
            )
            if payload.timeout_seconds is not None:
                return await asyncio.wait_for(coro, timeout=payload.timeout_seconds)
            return await coro
        finally:
            current_agent_run.reset(run_token)
            current_session.reset(session_token)
