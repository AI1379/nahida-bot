"""Coarse orchestration policy hooks."""

from __future__ import annotations

from collections.abc import Sequence

from nahida_bot.agent.orchestration.models import SubagentSpec
from nahida_bot.agent.providers import ToolDefinition


class OrchestrationPolicy:
    """Default coarse policy for the local orchestration MVP."""

    def __init__(self, *, max_child_agents_per_run: int = 5) -> None:
        self.max_child_agents_per_run = max_child_agents_per_run

    async def can_spawn(
        self,
        requester_session_id: str,
        spec: SubagentSpec,
        *,
        active_child_count: int,
        depth: int,
    ) -> None:
        if depth > 0:
            raise PermissionError("Subagents cannot spawn nested subagents.")
        if active_child_count >= self.max_child_agents_per_run:
            raise PermissionError(
                "Maximum active subagent count reached for this run/session."
            )
        if not requester_session_id:
            raise PermissionError("No requester session is available.")
        if not spec.task.strip():
            raise ValueError("Subagent task must not be empty.")

    async def can_read_session(
        self, requester_session_id: str, target_session_id: str
    ) -> None:
        if (
            target_session_id != requester_session_id
            and requester_session_id not in target_session_id
        ):
            raise PermissionError("Session is outside the requester scope.")

    async def can_send_session(
        self, requester_session_id: str, target_session_id: str
    ) -> None:
        await self.can_read_session(requester_session_id, target_session_id)

    async def filter_tools_for_child(
        self,
        requester_session_id: str,
        spec: SubagentSpec,
        available_tools: Sequence[ToolDefinition],
    ) -> Sequence[ToolDefinition]:
        denied = set(spec.tool_denylist) | {
            "agent_spawn",
            "agent_yield",
            "agent_wait",
            "agent_stop",
            "sessions_send",
        }
        allowed = set(spec.tool_allowlist)
        result: list[ToolDefinition] = []
        for tool in available_tools:
            if tool.name in denied:
                continue
            if allowed and tool.name not in allowed:
                continue
            result.append(tool)
        return result
