"""Shared agent execution pipeline for message dispatch and cron fires."""

from __future__ import annotations

from typing import TYPE_CHECKING, AbstractSet, Any

import structlog

from nahida_bot.agent.context import ContextMessage
from nahida_bot.agent.memory.models import ConversationTurn
from nahida_bot.agent.providers import ToolDefinition

if TYPE_CHECKING:
    from nahida_bot.agent.loop import AgentLoop, AgentRunResult
    from nahida_bot.agent.memory.store import MemoryStore
    from nahida_bot.agent.providers.manager import ProviderManager
    from nahida_bot.plugins.registry import ToolRegistry
    from nahida_bot.workspace.manager import WorkspaceManager

logger = structlog.get_logger(__name__)


class SessionRunner:
    """Resolve deps, run agent, persist turns — shared by router and scheduler."""

    def __init__(
        self,
        *,
        agent_loop: AgentLoop | None = None,
        memory_store: MemoryStore | None = None,
        provider_manager: ProviderManager | None = None,
        workspace_manager: WorkspaceManager | None = None,
        tool_registry: ToolRegistry | None = None,
        max_history_turns: int = 50,
    ) -> None:
        self._agent = agent_loop
        self._memory = memory_store
        self._providers = provider_manager
        self._workspace = workspace_manager
        self._tools = tool_registry
        self._max_history_turns = max_history_turns

    @property
    def has_agent(self) -> bool:
        return self._agent is not None

    @property
    def agent(self) -> AgentLoop | None:
        return self._agent

    @agent.setter
    def agent(self, value: AgentLoop | None) -> None:
        self._agent = value

    @property
    def memory(self) -> MemoryStore | None:
        return self._memory

    @memory.setter
    def memory(self, value: MemoryStore | None) -> None:
        self._memory = value

    @property
    def provider_manager(self) -> ProviderManager | None:
        return self._providers

    @provider_manager.setter
    def provider_manager(self, value: ProviderManager | None) -> None:
        self._providers = value

    @property
    def tool_registry(self) -> ToolRegistry | None:
        return self._tools

    @tool_registry.setter
    def tool_registry(self, value: ToolRegistry | None) -> None:
        self._tools = value

    async def run(
        self,
        *,
        user_message: str,
        session_id: str,
        system_prompt: str,
        workspace_id: str | None = None,
        workspace_root: Any = None,
        tool_filter: AbstractSet[str] | None = None,
        source_tag: str = "user_input",
    ) -> AgentRunResult:
        if self._agent is None:
            raise RuntimeError("SessionRunner has no agent loop configured")

        provider_slot = await self._resolve_provider(session_id)
        history = await self._load_history(session_id, workspace_id=workspace_id)
        tools = self._collect_tools(tool_filter)

        if workspace_root is None and workspace_id is not None:
            workspace_root = self._resolve_workspace_root(workspace_id)

        run_kwargs: dict[str, Any] = {
            "user_message": user_message,
            "system_prompt": system_prompt,
            "history_messages": history,
        }
        if workspace_root is not None:
            run_kwargs["workspace_root"] = workspace_root
        if tools:
            run_kwargs["tools"] = tools
        if provider_slot is not None:
            run_kwargs["provider"] = provider_slot.provider
            run_kwargs["context_builder"] = provider_slot.context_builder

        result = await self._agent.run(**run_kwargs)
        await self._persist_turns(
            session_id, user_message, result, source_tag=source_tag
        )
        return result

    # ── Private helpers ──────────────────────────────────────

    async def _resolve_provider(self, session_id: str) -> Any:
        if self._providers is None:
            return None
        if self._memory is not None:
            meta = await self._memory.get_session_meta(session_id)
            if meta:
                model = meta.get("model")
                if model:
                    slot = self._providers.resolve_model(model)
                    if slot is not None:
                        return slot
                provider_id = meta.get("provider_id")
                if provider_id:
                    slot = self._providers.get(provider_id)
                    if slot is not None:
                        return slot
        return self._providers.default

    async def _load_history(
        self, session_id: str, *, workspace_id: str | None = None
    ) -> list[ContextMessage]:
        if self._memory is None:
            return []
        await self._memory.ensure_session(session_id, workspace_id=workspace_id)
        records = await self._memory.get_recent(
            session_id, limit=self._max_history_turns
        )
        return [
            ContextMessage(
                role=r.turn.role,  # type: ignore[arg-type]
                content=r.turn.content,
                source=r.turn.source,
            )
            for r in records
        ]

    def _collect_tools(
        self, tool_filter: AbstractSet[str] | None
    ) -> list[ToolDefinition]:
        if self._tools is None:
            return []
        return [
            ToolDefinition(
                name=entry.name,
                description=entry.description,
                parameters=entry.parameters,
            )
            for entry in self._tools.all()
            if tool_filter is None or entry.name not in tool_filter
        ]

    def _resolve_workspace_root(self, workspace_id: str | None) -> Any:
        if self._workspace is None or workspace_id is None:
            return None
        return self._workspace.workspace_path(workspace_id)

    async def _persist_turns(
        self,
        session_id: str,
        user_message: str,
        result: Any,
        *,
        source_tag: str,
    ) -> None:
        if self._memory is None:
            return
        user_turn = ConversationTurn(
            role="user", content=user_message, source=source_tag
        )
        await self._memory.append_turn(session_id, user_turn)
        if result.final_response:
            assistant_turn = ConversationTurn(
                role="assistant",
                content=result.final_response,
                source="agent_response",
            )
            await self._memory.append_turn(session_id, assistant_turn)
