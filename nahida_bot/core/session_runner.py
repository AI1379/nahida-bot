"""Shared agent execution pipeline for message dispatch and cron fires."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, AbstractSet, Any, cast

import structlog

from nahida_bot.agent.context import ContextMessage, ContextPart
from nahida_bot.agent.loop import AgentRunResult
from nahida_bot.agent.memory.consolidation import MemoryConsolidator
from nahida_bot.agent.memory.models import ConversationTurn, MemoryRecord
from nahida_bot.agent.memory.scope import resolve_scope_from_session
from nahida_bot.agent.providers import ToolDefinition
from nahida_bot.agent.retrieval import (
    DocumentStoreRetrievalAdapter,
    MemoryStoreRetrievalAdapter,
    RetrievalRequest,
    RetrievalResult,
    RetrievalScope,
    RetrievalService,
)
from nahida_bot.agent.storage.tokenization import build_fts_query
from nahida_bot.identity.policy import (
    memory_read_request_from_context,
    resolve_memory_read_scopes,
)
from nahida_bot.core.config import ContextConfig, MediaContextPolicy
from nahida_bot.core.context import current_attachments, current_session
from nahida_bot.core.logging import log_trace
from nahida_bot.core.message_context import (
    ENVELOPE_INSTRUCTION,
    HEARTBEAT_INSTRUCTION,
    PROACTIVE_JOIN_INSTRUCTION,
    SILENT_REPLY_INSTRUCTION,
    assistant_context,
    context_from_inbound,
    message_context_from_metadata,
    message_context_to_metadata,
    render_message_with_context,
    strip_envelope_prefix,
)
from nahida_bot.core.runtime_settings import (
    REASONING_EFFORTS,
    ReasoningRuntimeSettings,
    RuntimeSettings,
    current_runtime_settings,
    runtime_settings_from_meta,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from nahida_bot.agent.loop import AgentLoop, LoopEvent
    from nahida_bot.agent.media.resolver import MediaResolver
    from nahida_bot.agent.storage.embedding import EmbeddingProvider
    from nahida_bot.agent.memory.store import MemoryStore
    from nahida_bot.agent.storage.vector import VectorIndex
    from nahida_bot.agent.providers.base import ChatProvider, ModelCapabilities
    from nahida_bot.agent.providers.manager import ProviderManager
    from nahida_bot.agent.providers.router import ModelRouter
    from nahida_bot.core.channel_registry import ChannelRegistry
    from nahida_bot.core.config import MemoryRetrievalConfig, MultimodalConfig
    from nahida_bot.plugins.base import (
        InboundAttachment,
        InboundMessage,
        MessageContext,
    )
    from nahida_bot.plugins.registry import ToolRegistry
    from nahida_bot.workspace.manager import WorkspaceManager

logger = structlog.get_logger(__name__)

_FALLBACK_VISION_PROMPT = (
    "Describe this image in detail. Include any visible text (OCR). "
    "Note any safety concerns."
)
_GROUP_CONTEXT_HISTORY_OVERFETCH_FACTOR = 4
_GROUP_CONTEXT_HISTORY_OVERFETCH_MIN = 50


@dataclass(slots=True)
class ActiveRun:
    """Tracks one in-flight agent run for a session."""

    task: asyncio.Task[None]
    stop_event: asyncio.Event
    session_id: str
    started_at: float = field(default_factory=time.monotonic)


class ActiveRunTracker:
    """Per-session active agent run tracking with cancellation support."""

    def __init__(self) -> None:
        self._runs: dict[str, ActiveRun] = {}

    def start(
        self, session_id: str, task: asyncio.Task[None], stop_event: asyncio.Event
    ) -> None:
        if session_id in self._runs:
            raise RuntimeError(f"Agent run already active for session {session_id}")
        self._runs[session_id] = ActiveRun(
            task=task, stop_event=stop_event, session_id=session_id
        )

    def finish(self, session_id: str) -> None:
        self._runs.pop(session_id, None)

    def is_active(self, session_id: str) -> bool:
        return session_id in self._runs

    def get(self, session_id: str) -> ActiveRun | None:
        return self._runs.get(session_id)

    def request_stop(self, session_id: str) -> bool:
        run = self._runs.get(session_id)
        if run is None:
            return False
        # Signal a graceful stop only. We must NOT cancel the task here: the
        # agent loop checks ``stop_event`` at each step boundary and yields a
        # final ``done`` event carrying whatever assistant/tool messages were
        # produced so far, so SessionRunner can persist the partial turn.
        # Cancelling the task would inject CancelledError mid-await, skipping
        # that persistence path and dropping the user message + partial reply
        # from history. Blocking points are bounded by provider/tool timeouts,
        # so the loop always reaches a stop check eventually.
        run.stop_event.set()
        return True

    @property
    def all_runs(self) -> list[ActiveRun]:
        return list(self._runs.values())


class SessionRunner:
    """Resolve deps, run agent, persist turns — shared by router and scheduler."""

    def __init__(
        self,
        *,
        agent_loop: AgentLoop | None = None,
        memory_store: MemoryStore | None = None,
        provider_manager: ProviderManager | None = None,
        model_router: ModelRouter | None = None,
        workspace_manager: WorkspaceManager | None = None,
        tool_registry: ToolRegistry | None = None,
        max_history_turns: int = 200,
        context_config: ContextConfig | None = None,
        multimodal_config: MultimodalConfig | None = None,
        memory_retrieval_config: MemoryRetrievalConfig | None = None,
        memory_embedding_provider: EmbeddingProvider | None = None,
        memory_vector_index: VectorIndex | None = None,
        memory_embed_after_consolidation: bool = False,
        memory_consolidation_rule_based_enabled: bool = True,
        group_context_max_messages: int = 20,
        group_context_ttl_seconds: int = 900,
        group_context_max_chars: int = 4000,
        media_resolver: MediaResolver | None = None,
        channel_registry: ChannelRegistry | None = None,
        supplement_registry: Any | None = None,
        enable_silent_reply: bool = True,
        document_store_manager: Any | None = None,
        kb_auto_recall_config: Any | None = None,
    ) -> None:
        self._agent = agent_loop
        self._memory = memory_store
        self._memory_consolidator = (
            MemoryConsolidator(memory_store) if memory_store is not None else None
        )
        self._providers = provider_manager
        self._model_router = model_router
        self._workspace = workspace_manager
        self._tools = tool_registry
        self._max_history_turns = max_history_turns
        self._context_config = context_config or ContextConfig()
        self._multimodal_config = multimodal_config
        self._memory_retrieval_config = memory_retrieval_config
        self._memory_embedding_provider = memory_embedding_provider
        self._memory_vector_index = memory_vector_index
        self._memory_embed_after_consolidation = memory_embed_after_consolidation
        self._memory_consolidation_rule_based_enabled = (
            memory_consolidation_rule_based_enabled
        )
        self._group_context_max_messages = group_context_max_messages
        self._group_context_ttl_seconds = group_context_ttl_seconds
        self._group_context_max_chars = group_context_max_chars
        self._media_resolver = media_resolver
        self._channel_registry = channel_registry
        self._supplement_registry = supplement_registry
        self._enable_silent_reply = enable_silent_reply
        self._document_store_manager = document_store_manager
        self._kb_auto_recall_config = kb_auto_recall_config
        self._run_tracker = ActiveRunTracker()

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
        self._memory_consolidator = (
            MemoryConsolidator(value) if value is not None else None
        )

    @property
    def provider_manager(self) -> ProviderManager | None:
        return self._providers

    @provider_manager.setter
    def provider_manager(self, value: ProviderManager | None) -> None:
        self._providers = value

    @property
    def model_router(self) -> ModelRouter | None:
        return self._model_router

    @model_router.setter
    def model_router(self, value: ModelRouter | None) -> None:
        self._model_router = value

    def _resolve_task_model(
        self,
        task: str,
        *,
        explicit: str = "",
        default_spec: str = "",
        fallback: str = "disabled",
        legacy_provider_id: str = "",
    ) -> tuple[Any, str | None, str] | None:
        """Resolve an internal task model from one model spec string."""
        legacy_provider_id = legacy_provider_id.strip()
        if not explicit and legacy_provider_id and self._providers is not None:
            slot = self._providers.get(legacy_provider_id)
            if slot is not None:
                return slot, None, "legacy_provider"

        if self._model_router is not None:
            result = self._model_router.resolve_for_task(
                task,
                explicit=explicit,
                default_spec=default_spec,
                fallback=fallback,  # type: ignore[arg-type]
            )
            if result is not None:
                return result.slot, result.model, result.reason
            return None

        if self._providers is None:
            return None
        if explicit:
            resolved = self._providers.resolve_model_selection(explicit)
            if resolved is not None:
                slot, model = resolved
                return slot, model, "explicit"
        return None

    @property
    def memory_embedding_provider(self) -> Any | None:
        return self._memory_embedding_provider

    @memory_embedding_provider.setter
    def memory_embedding_provider(self, value: Any | None) -> None:
        self._memory_embedding_provider = value

    @property
    def memory_vector_index(self) -> Any | None:
        return self._memory_vector_index

    @memory_vector_index.setter
    def memory_vector_index(self, value: Any | None) -> None:
        self._memory_vector_index = value

    async def resolve_provider_for_session(
        self, session_id: str
    ) -> tuple[Any, str | None]:
        """Resolve the provider/model that should serve a session."""
        return await self._resolve_provider(session_id)

    def workspace_root_for(self, workspace_id: str | None) -> Any:
        """Resolve a workspace root path for background services."""
        return self._resolve_workspace_root(workspace_id)

    @property
    def tool_registry(self) -> ToolRegistry | None:
        return self._tools

    @tool_registry.setter
    def tool_registry(self, value: ToolRegistry | None) -> None:
        self._tools = value

    @property
    def supplement_registry(self) -> Any | None:
        return self._supplement_registry

    @supplement_registry.setter
    def supplement_registry(self, value: Any | None) -> None:
        self._supplement_registry = value

    @property
    def run_tracker(self) -> ActiveRunTracker:
        return self._run_tracker

    def _context_builder_for_model(
        self,
        provider: ChatProvider,
        capabilities: ModelCapabilities | None,
    ) -> Any:
        """Create a context builder whose budget matches the selected model."""
        from nahida_bot.agent.context import ContextBuilder, build_context_budget

        budget = build_context_budget(
            self._context_config,
            capabilities=capabilities,
        )
        return ContextBuilder(budget=budget, provider=provider)

    async def run(
        self,
        *,
        user_message: str,
        session_id: str,
        system_prompt: str,
        workspace_id: str | None = None,
        workspace_root: Any = None,
        attachments: list[InboundAttachment] | None = None,
        message_context: MessageContext | None = None,
        provider_id: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        tool_allowlist: AbstractSet[str] | None = None,
        tool_filter: AbstractSet[str] | None = None,
        source_tag: str = "user_input",
        stop_event: asyncio.Event | None = None,
    ) -> AgentRunResult:
        """Run the agent loop and return the final result (backward-compat)."""
        done: LoopEvent | None = None
        async for event in self.run_stream(
            user_message=user_message,
            session_id=session_id,
            system_prompt=system_prompt,
            workspace_id=workspace_id,
            workspace_root=workspace_root,
            attachments=attachments,
            message_context=message_context,
            provider_id=provider_id,
            model=model,
            reasoning_effort=reasoning_effort,
            tool_allowlist=tool_allowlist,
            tool_filter=tool_filter,
            source_tag=source_tag,
            stop_event=stop_event,
        ):
            if event.type == "done":
                done = event
        if done is None:
            return AgentRunResult(final_response="")
        return AgentRunResult(
            final_response=done.final_response or "",
            assistant_messages=list(done.assistant_messages or []),
            tool_messages=list(done.tool_messages or []),
            steps=done.steps,
            trace_id=done.trace_id,
            error=done.error,
        )

    async def run_stream(
        self,
        *,
        user_message: str,
        session_id: str,
        system_prompt: str,
        workspace_id: str | None = None,
        workspace_root: Any = None,
        attachments: list[InboundAttachment] | None = None,
        message_context: MessageContext | None = None,
        provider_id: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        tool_allowlist: AbstractSet[str] | None = None,
        tool_filter: AbstractSet[str] | None = None,
        source_tag: str = "user_input",
        agent_instruction: str = "",
        stop_event: asyncio.Event | None = None,
    ) -> AsyncIterator[LoopEvent]:
        """Run the agent loop, yielding :class:`LoopEvent` as progress happens.

        Text events are yielded immediately when the provider produces
        user-visible content, so callers can send them to the user without
        waiting for tool calls to complete.
        """
        if self._agent is None:
            raise RuntimeError("SessionRunner has no agent loop configured")

        attachments_for_turn = tuple(attachments or [])
        attachments_token = current_attachments.set(attachments_for_turn)
        runtime_settings = await self._load_runtime_settings(session_id)
        runtime_settings = self._apply_runtime_overrides(
            runtime_settings,
            reasoning_effort=reasoning_effort,
        )
        runtime_token = current_runtime_settings.set(runtime_settings)
        done_data: dict[str, Any] = {}
        logger.debug(
            "session_runner.run_stream_start",
            session_id=session_id,
            source_tag=source_tag,
            workspace_id=workspace_id or "",
            provider_id=provider_id or "",
            requested_model=model or "",
            reasoning_effort=reasoning_effort or "",
            user_message_chars=len(user_message),
            user_message_preview=user_message[:120],
            attachment_count=len(attachments_for_turn),
            attachment_kinds=[att.kind for att in attachments_for_turn],
            stop_requested=stop_event.is_set() if stop_event is not None else False,
            **_message_context_log_fields(message_context),
        )
        try:
            provider_slot, selected_model = await self._resolve_provider(
                session_id,
                provider_id=provider_id,
                model=model,
            )
            effective_model = (
                selected_model or provider_slot.default_model
                if provider_slot is not None
                else ""
            )
            capabilities = (
                provider_slot.resolve_capabilities(effective_model)
                if provider_slot is not None
                else None
            )
            context_builder = None
            context_budget = None
            if provider_slot is not None:
                context_builder = self._context_builder_for_model(
                    provider_slot.provider,
                    capabilities,
                )
                context_budget = context_builder.budget
            image_count = sum(1 for att in attachments_for_turn if att.kind == "image")
            logger.debug(
                "session_runner.route_selected",
                session_id=session_id,
                provider_id=provider_slot.id if provider_slot is not None else "",
                selected_model=selected_model or "",
                effective_model=effective_model,
                image_input=bool(capabilities and capabilities.image_input),
                context_max_tokens=(
                    context_budget.max_tokens if context_budget is not None else None
                ),
                context_reserved_tokens=(
                    context_budget.reserved_tokens
                    if context_budget is not None
                    else None
                ),
                context_soft_token_limit=(
                    context_budget.soft_token_limit
                    if context_budget is not None
                    else None
                ),
                image_count=image_count,
                attachment_count=len(attachments_for_turn),
                image_fallback_mode=(
                    self._multimodal_config.image_fallback_mode
                    if self._multimodal_config is not None
                    else ""
                ),
                media_context_policy=(
                    self._multimodal_config.media_context_policy
                    if self._multimodal_config is not None
                    else ""
                ),
            )

            recent_records = await self._load_recent_records(
                session_id,
                workspace_id=workspace_id,
                include_observed_surplus=bool(
                    message_context is not None and message_context.chat_type == "group"
                ),
            )
            history = await self._build_history_context(
                session_id,
                recent_records,
                capabilities=capabilities,
            )
            observed_context = await self._load_observed_group_context(
                session_id,
                records=recent_records,
                current_message_context=message_context,
                current_message_content=user_message,
            )
            if observed_context is not None:
                history.append(observed_context)
                logger.debug(
                    "session_runner.group_observed_context_added",
                    session_id=session_id,
                    observed_context_chars=len(observed_context.content),
                    history_count=len(history),
                )
            relevant_memory = await self._load_relevant_memory(
                user_message, session_id=session_id
            )
            relevant_kb = await self._load_relevant_knowledge(
                user_message, session_id=session_id
            )
            if relevant_memory:
                history = [relevant_memory, *history]
                logger.debug(
                    "session_runner.relevant_memory_added",
                    session_id=session_id,
                    relevant_memory_chars=len(relevant_memory.content),
                    history_count=len(history),
                )
            if relevant_kb:
                history = [relevant_kb, *history]
                logger.debug(
                    "session_runner.relevant_kb_added",
                    session_id=session_id,
                    relevant_kb_chars=len(relevant_kb.content),
                    history_count=len(history),
                )
            tools = self._collect_tools(
                tool_filter,
                tool_allowlist=tool_allowlist,
                capabilities=capabilities,
            )
            logger.debug(
                "session_runner.tools_collected",
                session_id=session_id,
                provider_id=provider_slot.id if provider_slot is not None else "",
                effective_model=effective_model,
                tool_count=len(tools),
                tool_names=[tool.name for tool in tools[:50]],
                tool_denylist=sorted(tool_filter) if tool_filter is not None else [],
                tool_allowlist=(
                    sorted(tool_allowlist) if tool_allowlist is not None else []
                ),
                model_tool_calling=(
                    capabilities.tool_calling if capabilities is not None else None
                ),
            )
            visible_user_message = render_message_with_context(
                user_message,
                message_context,
                role="user",
            )
            user_parts = await self._build_user_parts(
                visible_user_message,
                list(attachments_for_turn),
                capabilities=capabilities,
            )
            persisted_image_descriptions: dict[str, str] = {}
            if (
                not bool(capabilities and capabilities.image_input)
                and self._multimodal_config is not None
                and self._multimodal_config.image_fallback_mode == "auto"
            ):
                persisted_image_descriptions = self._image_descriptions_from_parts(
                    user_parts
                )
            logger.debug(
                "session_runner.context_inputs_ready",
                session_id=session_id,
                history_count=len(history),
                history_roles=[m.role for m in history],
                tool_count=len(tools),
                user_part_types=[part.type for part in user_parts],
                workspace_id=workspace_id or "",
            )

            if workspace_root is None and workspace_id is not None:
                workspace_root = self._resolve_workspace_root(workspace_id)

            effective_system_prompt = self._build_system_prompt(
                system_prompt,
                message_context,
                source_tag=source_tag,
                agent_instruction=agent_instruction,
                enable_silent_reply=self._enable_silent_reply,
            )

            run_kwargs: dict[str, Any] = {
                "user_message": visible_user_message,
                "system_prompt": effective_system_prompt,
                "history_messages": history,
            }
            if user_parts:
                run_kwargs["user_parts"] = user_parts
            if workspace_root is not None:
                run_kwargs["workspace_root"] = workspace_root
            if tools:
                run_kwargs["tools"] = tools
            if provider_slot is not None:
                run_kwargs["provider"] = provider_slot.provider
                run_kwargs["context_builder"] = (
                    context_builder or provider_slot.context_builder
                )
            if selected_model is not None:
                run_kwargs["model"] = selected_model

            logger.debug(
                "session_runner.agent_run_start",
                session_id=session_id,
                provider_id=provider_slot.id if provider_slot is not None else "",
                selected_model=selected_model or "",
                effective_model=effective_model,
                history_count=len(history),
                tool_count=len(tools),
                user_part_count=len(user_parts),
            )
            if stop_event is not None:
                run_kwargs["stop_event"] = stop_event

            async for event in self._agent.run_stream(**run_kwargs):
                logger.debug(
                    "session_runner.agent_event",
                    session_id=session_id,
                    event_type=event.type,
                    trace_id=event.trace_id or "",
                    text_chars=len(event.text or ""),
                    reasoning_chars=len(event.reasoning or ""),
                    final_response_chars=len(event.final_response or ""),
                    tool_names=list(event.tool_names or []),
                    steps=event.steps,
                    error=event.error or "",
                )
                if event.type == "done":
                    done_data = {
                        "final_response": event.final_response or "",
                        "assistant_messages": list(event.assistant_messages or []),
                        "tool_messages": list(event.tool_messages or []),
                        "steps": event.steps,
                        "trace_id": event.trace_id,
                        "error": event.error,
                        "total_usage": event.total_usage,
                    }
                yield event
            logger.debug(
                "session_runner.agent_run_done",
                session_id=session_id,
                trace_id=done_data.get("trace_id"),
                provider_id=provider_slot.id if provider_slot is not None else "",
                effective_model=effective_model,
                steps=done_data.get("steps"),
                error=done_data.get("error"),
                response_chars=len(str(done_data.get("final_response", ""))),
                assistant_message_count=len(done_data.get("assistant_messages", [])),
                tool_message_count=len(done_data.get("tool_messages", [])),
            )
            await self._persist_turns(
                session_id,
                user_message,
                AgentRunResult(**done_data)
                if done_data
                else AgentRunResult(final_response=""),
                attachments=list(attachments_for_turn),
                image_descriptions=persisted_image_descriptions,
                message_context=message_context,
                source_tag=source_tag,
                workspace_id=workspace_id,
                workspace_root=workspace_root,
            )
        finally:
            current_runtime_settings.reset(runtime_token)
            current_attachments.reset(attachments_token)

    # ── Public helpers (used by image_understand tool) ─────────

    async def _load_runtime_settings(self, session_id: str) -> RuntimeSettings:
        """Load per-session runtime settings from memory metadata."""
        if self._memory is None:
            return runtime_settings_from_meta(None)
        try:
            meta = await self._memory.get_session_meta(session_id)
            return runtime_settings_from_meta(meta)
        except Exception:
            logger.warning(
                "session_runner.runtime_settings_load_failed",
                session_id=session_id,
                exc_info=True,
            )
            return runtime_settings_from_meta(None)

    @staticmethod
    def _apply_runtime_overrides(
        settings: RuntimeSettings,
        *,
        reasoning_effort: str | None = None,
    ) -> RuntimeSettings:
        effort = str(reasoning_effort or "").strip().lower()
        if not effort:
            return settings
        if effort not in REASONING_EFFORTS:
            logger.warning(
                "session_runner.runtime_override_ignored",
                key="reasoning.effort",
                value=reasoning_effort,
            )
            return settings
        return replace(
            settings,
            reasoning=ReasoningRuntimeSettings(
                show=settings.reasoning.show,
                effort=effort,
            ),
        )

    async def handle_image_understand_tool(
        self, *, media_id: str = "latest", question: str = ""
    ) -> str:
        """Handle the ``image_understand`` tool call.

        Loads the referenced image from the current session's memory, resolves
        it, and calls the fallback vision provider for a description.
        """
        if self._providers is None:
            return "Error: no provider manager available"
        if self._multimodal_config is None:
            return "Error: multimodal not configured"

        routed = self._resolve_task_model(
            "image_fallback",
            explicit=_legacy_model_spec(
                provider_id=self._multimodal_config.image_fallback_provider,
                model=self._multimodal_config.image_fallback_model,
            ),
            default_spec="vision",
            fallback="disabled",
            legacy_provider_id=self._multimodal_config.image_fallback_provider,
        )
        if routed is None:
            return "Error: no fallback vision model configured"
        slot, fallback_model, _reason = routed

        # Load recent turns to find the attachment

        attachment = await self._find_attachment_in_history(media_id)
        if attachment is None:
            return f"Error: no image found for media_id '{media_id}'"

        # Resolve the image
        resolved = await self._resolve_attachment(attachment)

        # Build vision request
        prompt = question if question else _FALLBACK_VISION_PROMPT
        content_parts: list[ContextPart] = [
            ContextPart(type="text", text=prompt),
        ]
        if resolved.base64_data:
            content_parts.append(
                ContextPart(
                    type="image_base64",
                    data=resolved.base64_data,
                    mime_type=resolved.mime_type,
                    media_id=resolved.media_id,
                )
            )
        elif resolved.local_path and attachment.url:
            content_parts.append(
                ContextPart(
                    type="image_url",
                    url=attachment.url,
                    media_id=resolved.media_id,
                    mime_type=resolved.mime_type,
                )
            )
        else:
            return f"Error: could not resolve image '{media_id}' to viewable form"

        vision_msg = ContextMessage(
            role="user",
            source="image_understand_tool",
            content=prompt,
            parts=content_parts,
        )

        chat_kwargs: dict[str, Any] = {}
        if fallback_model:
            chat_kwargs["model"] = fallback_model

        try:
            response = await slot.provider.chat(
                messages=[vision_msg],
                **chat_kwargs,
            )
            return response.content or "Error: empty response from vision provider"
        except Exception as exc:
            return f"Error: vision provider call failed: {exc}"

    # ── Private helpers ──────────────────────────────────────

    async def _resolve_provider(
        self,
        session_id: str,
        *,
        provider_id: str | None = None,
        model: str | None = None,
    ) -> tuple[Any, str | None]:
        if self._providers is None:
            logger.debug(
                "session_runner.provider_resolved",
                session_id=session_id,
                reason="no_provider_manager",
            )
            return None, None
        direct_model = str(model or "").strip()
        direct_provider_id = str(provider_id or "").strip()
        if direct_provider_id:
            slot = self._providers.get(direct_provider_id)
            if slot is not None:
                provider_model = direct_model
                if "/" in provider_model:
                    prefix, _, suffix = provider_model.partition("/")
                    if prefix == direct_provider_id:
                        provider_model = suffix
                if provider_model and slot.supports_model(provider_model):
                    override = (
                        provider_model if provider_model != slot.default_model else None
                    )
                    logger.debug(
                        "session_runner.provider_resolved",
                        session_id=session_id,
                        reason="direct_provider_and_model",
                        provider_id=slot.id,
                        requested_model=direct_model,
                        selected_model=override or "",
                        effective_model=provider_model,
                        default_model=slot.default_model,
                    )
                    return slot, override
                if not provider_model:
                    logger.debug(
                        "session_runner.provider_resolved",
                        session_id=session_id,
                        reason="direct_provider_id",
                        provider_id=slot.id,
                        default_model=slot.default_model,
                    )
                    return slot, None
                logger.warning(
                    "session_runner.direct_provider_model_mismatch",
                    session_id=session_id,
                    provider_id=slot.id,
                    requested_model=direct_model,
                    provider_model=provider_model,
                    available_models=slot.available_models,
                )
                return slot, None
            else:
                logger.debug(
                    "session_runner.direct_provider_id_not_found",
                    session_id=session_id,
                    provider_id=direct_provider_id,
                )

        if direct_model:
            resolved = self._providers.resolve_model_selection(direct_model)
            if resolved is not None:
                slot, provider_model = resolved
                override = (
                    provider_model if provider_model != slot.default_model else None
                )
                logger.debug(
                    "session_runner.provider_resolved",
                    session_id=session_id,
                    reason="direct_model",
                    provider_id=slot.id,
                    requested_model=direct_model,
                    selected_model=override or "",
                    effective_model=provider_model,
                    default_model=slot.default_model,
                )
                return slot, override
            logger.debug(
                "session_runner.direct_model_not_found",
                session_id=session_id,
                requested_model=direct_model,
            )
        if self._memory is not None:
            meta = await self._memory.get_session_meta(session_id)
            logger.debug(
                "session_runner.session_meta_loaded",
                session_id=session_id,
                provider_id=meta.get("provider_id", "") if meta else "",
                model=meta.get("model", "") if meta else "",
                has_meta=bool(meta),
            )
            if meta:
                model = str(meta.get("model") or "").strip()
                provider_id = str(meta.get("provider_id") or "").strip()
                if provider_id:
                    slot = self._providers.get(provider_id)
                    if slot is not None:
                        provider_model = model
                        if "/" in provider_model:
                            prefix, _, suffix = provider_model.partition("/")
                            if prefix == provider_id:
                                provider_model = suffix
                        if provider_model and slot.supports_model(provider_model):
                            override = (
                                provider_model
                                if provider_model != slot.default_model
                                else None
                            )
                            logger.debug(
                                "session_runner.provider_resolved",
                                session_id=session_id,
                                reason="session_provider_and_model",
                                provider_id=slot.id,
                                requested_model=model,
                                selected_model=override or "",
                                effective_model=provider_model,
                                default_model=slot.default_model,
                            )
                            return slot, override
                        if provider_model:
                            logger.warning(
                                "session_runner.provider_model_mismatch",
                                session_id=session_id,
                                provider_id=slot.id,
                                requested_model=model,
                                provider_model=provider_model,
                                available_models=slot.available_models,
                            )
                        else:
                            logger.debug(
                                "session_runner.provider_resolved",
                                session_id=session_id,
                                reason="session_provider_id",
                                provider_id=slot.id,
                                default_model=slot.default_model,
                            )
                            return slot, None
                    else:
                        logger.debug(
                            "session_runner.provider_id_not_found",
                            session_id=session_id,
                            provider_id=provider_id,
                        )

                if model:
                    resolved = self._providers.resolve_model_selection(model)
                    if resolved is not None:
                        slot, provider_model = resolved
                        override = (
                            provider_model
                            if provider_model != slot.default_model
                            else None
                        )
                        logger.debug(
                            "session_runner.provider_resolved",
                            session_id=session_id,
                            reason="session_model",
                            provider_id=slot.id,
                            requested_model=model,
                            selected_model=override or "",
                            effective_model=provider_model,
                            default_model=slot.default_model,
                        )
                        return slot, override
                    logger.debug(
                        "session_runner.provider_model_not_found",
                        session_id=session_id,
                        requested_model=model,
                    )
        slot = self._providers.default
        logger.debug(
            "session_runner.provider_resolved",
            session_id=session_id,
            reason="default_provider",
            provider_id=slot.id if slot is not None else "",
            default_model=slot.default_model if slot is not None else "",
        )
        return slot, None

    async def _load_history(
        self,
        session_id: str,
        *,
        workspace_id: str | None = None,
        capabilities: ModelCapabilities | None = None,
        include_observed_surplus: bool = False,
    ) -> list[ContextMessage]:
        records = await self._load_recent_records(
            session_id,
            workspace_id=workspace_id,
            include_observed_surplus=include_observed_surplus,
        )
        return await self._build_history_context(
            session_id,
            records,
            capabilities=capabilities,
        )

    async def _load_recent_records(
        self,
        session_id: str,
        *,
        workspace_id: str | None = None,
        include_observed_surplus: bool = False,
    ) -> list[MemoryRecord]:
        if self._memory is None:
            logger.debug(
                "session_runner.history_skipped",
                session_id=session_id,
                reason="no_memory_store",
            )
            return []
        await self._memory.ensure_session(session_id, workspace_id=workspace_id)
        history_query_limit = self._history_query_limit(
            include_observed_surplus=include_observed_surplus,
        )
        records = await self._memory.get_recent(session_id, limit=history_query_limit)
        logger.debug(
            "session_runner.history_loaded",
            session_id=session_id,
            workspace_id=workspace_id or "",
            record_count=len(records),
            max_history_turns=self._max_history_turns,
            history_query_limit=history_query_limit,
            roles=[r.turn.role for r in records],
            sources=[r.turn.source for r in records],
        )
        log_trace(
            logger,
            "session_runner.history_trace",
            session_id=session_id,
            records=[
                {
                    "role": r.turn.role,
                    "source": r.turn.source,
                    "content_chars": len(r.turn.content),
                    "content_preview": r.turn.content[:200],
                    "has_metadata": bool(r.turn.metadata),
                    "metadata_keys": sorted(r.turn.metadata.keys())
                    if isinstance(r.turn.metadata, dict)
                    else [],
                }
                for r in records
            ],
        )
        return records

    async def _build_history_context(
        self,
        session_id: str,
        records: list[MemoryRecord],
        *,
        capabilities: ModelCapabilities | None = None,
    ) -> list[ContextMessage]:
        messages: list[ContextMessage] = []
        for r in records:
            metadata = r.turn.metadata
            if isinstance(metadata, dict) and metadata.get("observed_only") is True:
                continue
            parts = (
                await self._reconstruct_parts_for_history(metadata)
                if r.turn.role == "user"
                else []
            )
            turn_context = message_context_from_metadata(metadata)
            visible_content = render_message_with_context(
                r.turn.content,
                turn_context,
                role=r.turn.role,
            )
            if r.turn.role == "user" and parts:
                parts = self._prepend_text_part(visible_content, parts)
            reasoning = None
            reasoning_signature = None
            has_redacted = False
            if r.turn.role == "assistant" and isinstance(metadata, dict):
                reasoning = metadata.get("reasoning")
                reasoning_signature = metadata.get("reasoning_signature")
                has_redacted = metadata.get("has_redacted_thinking", False)

            messages.append(
                ContextMessage(
                    role=r.turn.role,  # type: ignore[arg-type]
                    content=visible_content,
                    source=r.turn.source,
                    metadata=metadata,
                    parts=parts,
                    reasoning=reasoning,
                    reasoning_signature=reasoning_signature,
                    has_redacted_thinking=has_redacted,
                )
            )

        if len(messages) > self._max_history_turns:
            messages = messages[-self._max_history_turns :]

        # Apply media context policy to history
        if self._multimodal_config is not None and any(
            m.parts for m in messages if m.role == "user"
        ):
            messages = self._apply_media_context_policy(
                messages,
                policy=self._multimodal_config.media_context_policy,
                capabilities=capabilities,
            )
            logger.debug(
                "session_runner.history_media_policy_applied",
                session_id=session_id,
                policy=self._multimodal_config.media_context_policy,
                message_count=len(messages),
                part_count=sum(len(m.parts) for m in messages),
            )

        logger.debug(
            "session_runner.history_context_built",
            session_id=session_id,
            message_count=len(messages),
            protocol_summary=self._context_protocol_summary(messages),
        )

        return messages

    def _history_query_limit(self, *, include_observed_surplus: bool = False) -> int:
        """Return how many raw turns to fetch before filtering observed-only rows."""
        if not include_observed_surplus or self._group_context_max_messages <= 0:
            return self._max_history_turns

        # Observed-only rows are stored in the same session stream but do not count
        # toward normal dialogue history. Fetch a bounded surplus so active group
        # chatter does not crowd out the last N user/assistant turns.
        surplus = max(
            self._group_context_max_messages * _GROUP_CONTEXT_HISTORY_OVERFETCH_FACTOR,
            _GROUP_CONTEXT_HISTORY_OVERFETCH_MIN,
        )
        return self._max_history_turns + surplus

    async def _load_observed_group_context(
        self,
        session_id: str,
        *,
        records: list[MemoryRecord] | None = None,
        current_message_context: MessageContext | None,
        current_message_content: str = "",
    ) -> ContextMessage | None:
        """Load recent observed-only group messages for a triggered group turn."""
        if (
            self._memory is None
            or self._group_context_max_messages <= 0
            or self._group_context_max_chars <= 0
            or current_message_context is None
            or current_message_context.chat_type != "group"
        ):
            return None

        if records is None:
            records = await self._memory.get_recent(
                session_id,
                limit=self._history_query_limit(include_observed_surplus=True),
            )
        cutoff: datetime | None = None
        if self._group_context_ttl_seconds > 0:
            cutoff = datetime.now(UTC) - timedelta(
                seconds=self._group_context_ttl_seconds
            )

        selected: list[MemoryRecord] = []
        for record in reversed(records):
            metadata = record.turn.metadata
            if (
                not isinstance(metadata, dict)
                or metadata.get("observed_only") is not True
            ):
                continue
            if cutoff is not None and record.turn.created_at < cutoff:
                continue
            if self._is_current_observed_record(
                record,
                current_message_context=current_message_context,
                current_message_content=current_message_content,
            ):
                continue
            selected.append(record)
            if len(selected) >= self._group_context_max_messages:
                break

        if not selected:
            return None

        lines = [
            "Recent group chat context observed before the current trigger.",
            "These messages did not directly summon the bot; use them only as nearby context.",
        ]
        remaining = self._group_context_max_chars
        for record in reversed(selected):
            visible = render_message_with_context(
                record.turn.content,
                message_context_from_metadata(record.turn.metadata),
                role=record.turn.role,
            )
            line = f"- {visible}".replace("\n", "\n  ")
            if len(line) > remaining:
                line = line[:remaining].rstrip() + "..."
            lines.append(line)
            remaining -= len(line)
            if remaining <= 0:
                break

        if len(lines) <= 2:
            return None
        return ContextMessage(
            role="system",
            source="group_observed_context",
            content="\n".join(lines),
            metadata={
                "observed_message_count": len(lines) - 2,
                "ttl_seconds": self._group_context_ttl_seconds,
            },
        )

    @staticmethod
    def _is_current_observed_record(
        record: MemoryRecord,
        *,
        current_message_context: MessageContext,
        current_message_content: str,
    ) -> bool:
        """Return true when an observed row appears to duplicate current input."""
        if record.turn.content != current_message_content:
            return False
        observed_context = message_context_from_metadata(record.turn.metadata)
        if observed_context is None:
            return False
        if observed_context.sender_id != current_message_context.sender_id:
            return False
        if observed_context.chat_id != current_message_context.chat_id:
            return False
        if observed_context.timestamp and current_message_context.timestamp:
            return observed_context.timestamp == current_message_context.timestamp
        return True

    async def _load_relevant_memory(
        self, query: str, *, session_id: str = ""
    ) -> ContextMessage | None:
        """Load a small relevant durable-memory context block for the current turn."""
        if self._memory is None or not query.strip():
            return None
        cfg = self._memory_retrieval_config
        limit = cfg.max_injected_items if cfg is not None else 5
        max_chars = cfg.max_injected_chars if cfg is not None else 4000
        if limit <= 0 or max_chars <= 0:
            return None
        fts_enabled = cfg.fts_enabled if cfg is not None else True
        vector_enabled = (
            cfg is not None
            and cfg.vector_enabled
            and self._memory_embedding_provider is not None
        )
        if not fts_enabled and not vector_enabled:
            return None
        if fts_enabled and not build_fts_query(query):
            return None

        # Identity-aware read cascade (issue #7, Phase 2): person -> account ->
        # chat -> global for private chats, chat -> global for groups. When
        # identity is off or the sender is unlinked this collapses to the V1
        # chat -> global cascade (or global-only for legacy sessions).
        read_request = memory_read_request_from_context(
            current_session.get(), session_id
        )
        scopes = tuple(
            RetrievalScope(scope_type=scope_type, scope_id=scope_id)
            for scope_type, scope_id in resolve_memory_read_scopes(read_request)
        )

        try:
            adapter = MemoryStoreRetrievalAdapter(
                memory_store=self._memory,
                embedding_provider=self._memory_embedding_provider,
                vector_index=self._memory_vector_index,
            )
            service = RetrievalService({"memory": adapter})
            results = await service.retrieve(
                RetrievalRequest(
                    query=query,
                    source_type="memory",
                    limit=limit,
                    scopes=scopes,
                    fts_enabled=fts_enabled,
                    vector_enabled=vector_enabled,
                    hybrid_enabled=cfg.hybrid_enabled if cfg is not None else True,
                )
            )
        except Exception as exc:
            logger.warning("session_runner.memory_search_failed", error=str(exc))
            return None
        if not results:
            return None

        lines = [
            "Relevant durable memory:",
            "Treat memory as helpful context, not unquestionable truth. Current user instructions and current files take precedence.",
        ]
        remaining = max_chars
        for result in results:
            kind = str(result.metadata.get("kind") or "memory")
            title = result.title
            content = result.text.strip()
            item_id = result.result_id
            if not content:
                continue
            prefix = f"- [{kind}"
            if item_id:
                prefix += f" {item_id}"
            prefix += "] "
            if title:
                prefix += f"{title}: "
            allowance = max(remaining - len(prefix), 0)
            if allowance <= 0:
                break
            if len(content) > allowance:
                content = content[:allowance].rstrip() + "..."
            line = prefix + content
            lines.append(line)
            remaining -= len(line)
            if remaining <= 0:
                break

        if len(lines) <= 2:
            return None
        # Derive the backend label from the mode the adapter actually executed,
        # not from the request flags: the adapter may degrade hybrid/vector to fts
        # (e.g. when the store lacks the hybrid/vector method), so the request flags
        # can diverge from what really ran.
        backend = {
            "hybrid": "items_hybrid",
            "vector": "items_vector",
            "fts": "items",
            "none": "items",
        }.get(results[0].mode, "items")
        return ContextMessage(
            role="system",
            source="long_term_memory",
            content="\n".join(lines),
            metadata={
                "memory_backend": backend,
                "memory_count": len(lines) - 2,
            },
        )

    async def _load_relevant_knowledge(
        self, query: str, *, session_id: str = ""
    ) -> ContextMessage | None:
        """Load a small relevant KB context block for the current turn.

        Searches every KB collection with a tiny per-collection budget (FTS-only),
        merges results across collections by score, and wraps the top entries as a
        lightweight system-level ``ContextMessage``.  Returns ``None`` when KB
        auto-recall is disabled, the manager is unavailable, or nothing is found.
        """
        manager = self._document_store_manager
        cfg = self._kb_auto_recall_config
        if manager is None or cfg is None:
            return None
        if not cfg.enabled:
            return None
        limit = cfg.max_items
        max_chars = cfg.max_chars
        if limit <= 0 or max_chars <= 0:
            return None
        if not query.strip():
            return None

        # FTS-only: keep it fast and low-cost for the automatic path.
        fts_query = build_fts_query(query)
        if not fts_query:
            return None

        # Search every collection with a tiny per-collection budget, then merge.
        all_results: list[RetrievalResult] = []
        try:
            for name in manager.list_collections():
                store = manager.get(name)
                if store is None:
                    continue
                adapter = DocumentStoreRetrievalAdapter(
                    collection_name=name,
                    store=store,
                )
                service = RetrievalService({"knowledge_base": adapter})
                try:
                    hits = await service.retrieve(
                        RetrievalRequest(
                            query=query,
                            source_type="knowledge_base",
                            collection=name,
                            limit=1,
                            fts_enabled=True,
                            vector_enabled=False,
                            hybrid_enabled=False,
                            min_score=cfg.min_score,
                        )
                    )
                except Exception:
                    logger.debug(
                        "session_runner.kb_auto_recall_collection_failed",
                        collection=name,
                    )
                    continue
                all_results.extend(hits)
        except Exception as exc:
            # The document-store manager itself raised (e.g. transient DB error
            # on list_collections/get) — degrade like _load_relevant_memory does
            # rather than aborting the whole agent turn.
            logger.warning("session_runner.kb_auto_recall_failed", error=str(exc))
            return None

        if not all_results:
            return None

        # FTS BM25 scores are ascending (smaller = more relevant); sort
        # accordingly so the best (most negative) hits come first.
        all_results.sort(key=lambda r: r.score)
        # Dedup by (collection, doc_id) — collections are physically isolated
        # tables so doc_ids can collide across collections.
        seen: set[str] = set()
        top: list[RetrievalResult] = []
        for r in all_results:
            key = f"{r.metadata.get('collection', '')}:{r.result_id}"
            if key in seen:
                continue
            seen.add(key)
            seen.add(r.result_id)
            top.append(r)
            if len(top) >= limit:
                break

        if not top:
            return None

        lines = [
            "Relevant knowledge base snippets:",
            "Treat snippets as helpful background context, not unquestionable truth."
            " Use kb_search to dig deeper when needed.",
        ]
        remaining = max_chars
        for result in top:
            collection = str(result.metadata.get("collection", ""))
            title = result.title.strip()
            content = result.text.strip()
            source_path = str(result.metadata.get("path", ""))
            if not content:
                continue
            prefix = f"- [{collection}] "
            if title:
                prefix += f"{title}"
                if source_path:
                    prefix += f" [{source_path}]"
                prefix += ": "
            allowance = max(remaining - len(prefix), 0)
            if allowance <= 0:
                break
            if len(content) > allowance:
                content = content[:allowance].rstrip() + "..."
            line = prefix + content
            lines.append(line)
            remaining -= len(line)
            if remaining <= 0:
                break

        if len(lines) <= 2:
            return None
        return ContextMessage(
            role="system",
            source="knowledge_base",
            content="\n".join(lines),
            metadata={
                "kb_backend": "fts",
                "kb_count": len(lines) - 2,
            },
        )

    @staticmethod
    def _prepend_text_part(
        content: str,
        parts: list[ContextPart],
    ) -> list[ContextPart]:
        if not content:
            return parts
        if parts and parts[0].type == "text":
            return parts
        return [ContextPart(type="text", text=content), *parts]

    @staticmethod
    def _reconstruct_parts(
        metadata: dict[str, Any] | None,
    ) -> list[ContextPart]:
        if not metadata or "attachments" not in metadata:
            return []
        parts: list[ContextPart] = []
        for att in metadata["attachments"]:
            if att.get("kind") != "image":
                continue
            if att.get("url"):
                parts.append(
                    ContextPart(
                        type="image_url",
                        url=att["url"],
                        media_id=att.get("platform_id", ""),
                        mime_type=att.get("mime_type", ""),
                    )
                )
            elif att.get("path"):
                parts.append(
                    ContextPart(
                        type="image_url",
                        url=att["path"],
                        media_id=att.get("platform_id", ""),
                        mime_type=att.get("mime_type", ""),
                    )
                )
            elif att.get("alt_text"):
                parts.append(
                    ContextPart(
                        type="image_description",
                        text=att["alt_text"],
                        media_id=att.get("platform_id", ""),
                    )
                )
        return parts

    async def _reconstruct_parts_for_history(
        self,
        metadata: dict[str, Any] | None,
    ) -> list[ContextPart]:
        """Rebuild provider-safe image parts from persisted attachment metadata."""
        attachments = self._attachments_from_metadata(metadata)
        parts: list[ContextPart] = []
        for attachment in attachments:
            if attachment.alt_text:
                parts.append(
                    ContextPart(
                        type="image_description",
                        text=attachment.alt_text,
                        media_id=attachment.platform_id,
                        mime_type=attachment.mime_type,
                    )
                )
                continue

            if not attachment.path and not attachment.url:
                parts.append(
                    ContextPart(
                        type="image_description",
                        text=f"[Image: {attachment.platform_id}]",
                        media_id=attachment.platform_id,
                        mime_type=attachment.mime_type,
                    )
                )
                continue

            resolved = await self._resolve_attachment(attachment)
            if resolved.base64_data:
                parts.append(
                    ContextPart(
                        type="image_base64",
                        data=resolved.base64_data,
                        media_id=resolved.media_id,
                        mime_type=resolved.mime_type,
                    )
                )
            elif resolved.description:
                parts.append(
                    ContextPart(
                        type="image_description",
                        text=resolved.description,
                        media_id=resolved.media_id,
                        mime_type=resolved.mime_type,
                    )
                )
            else:
                parts.append(
                    ContextPart(
                        type="image_description",
                        text=f"[Image: {attachment.platform_id}]",
                        media_id=attachment.platform_id,
                        mime_type=attachment.mime_type,
                    )
                )
        return parts

    @staticmethod
    def _attachments_from_metadata(
        metadata: dict[str, Any] | None,
    ) -> list[InboundAttachment]:
        """Recover image attachments from persisted turn metadata."""
        if not metadata or "attachments" not in metadata:
            return []

        from nahida_bot.plugins.base import InboundAttachment

        attachments: list[InboundAttachment] = []
        raw_attachments = metadata.get("attachments")
        if not isinstance(raw_attachments, list):
            return attachments

        for raw in raw_attachments:
            if not isinstance(raw, dict) or raw.get("kind") != "image":
                continue
            raw_metadata = raw.get("metadata")
            attachments.append(
                InboundAttachment(
                    kind="image",
                    platform_id=str(raw.get("platform_id", "")),
                    url=str(raw.get("url", "")),
                    path=str(raw.get("path", "")),
                    mime_type=str(raw.get("mime_type", "")),
                    file_size=_safe_int(raw.get("file_size")),
                    width=_safe_int(raw.get("width")),
                    height=_safe_int(raw.get("height")),
                    alt_text=str(raw.get("description") or raw.get("alt_text") or ""),
                    metadata=dict(raw_metadata)
                    if isinstance(raw_metadata, dict)
                    else {},
                )
            )
        return attachments

    @staticmethod
    def _apply_media_context_policy(
        messages: list[ContextMessage],
        *,
        policy: MediaContextPolicy,
        capabilities: ModelCapabilities | None,
    ) -> list[ContextMessage]:
        """Apply media context policy to degrade historical image parts."""
        if capabilities is not None and not capabilities.image_input:
            policy = "description_only"

        if policy == "cache_aware":
            # Keep native images for the most recent user turns, degrade older
            user_indices = [i for i, m in enumerate(messages) if m.role == "user"]
            if not user_indices:
                return messages
            # Keep the last 2 user turns' images native, degrade the rest
            recent_threshold = (
                user_indices[-2] if len(user_indices) >= 2 else user_indices[-1]
            )

            result: list[ContextMessage] = []
            for i, msg in enumerate(messages):
                if msg.role == "user" and msg.parts and i < recent_threshold:
                    result.append(
                        ContextMessage(
                            role=msg.role,
                            content=msg.content,
                            source=msg.source,
                            metadata=msg.metadata,
                            parts=SessionRunner._degrade_image_parts(msg.parts),
                            reasoning=msg.reasoning,
                            reasoning_signature=msg.reasoning_signature,
                            has_redacted_thinking=msg.has_redacted_thinking,
                        )
                    )
                else:
                    result.append(msg)
            return result

        if policy == "description_only":
            return [
                (
                    ContextMessage(
                        role=m.role,
                        content=m.content,
                        source=m.source,
                        metadata=m.metadata,
                        parts=SessionRunner._degrade_image_parts(m.parts),
                        reasoning=m.reasoning,
                        reasoning_signature=m.reasoning_signature,
                        has_redacted_thinking=m.has_redacted_thinking,
                    )
                    if m.role == "user" and m.parts
                    else m
                )
                for m in messages
            ]

        if policy == "native_recent":
            user_indices = [i for i, m in enumerate(messages) if m.role == "user"]
            last_user = user_indices[-1] if user_indices else -1

            result: list[ContextMessage] = []
            for i, msg in enumerate(messages):
                if msg.role == "user" and msg.parts and i != last_user:
                    result.append(
                        ContextMessage(
                            role=msg.role,
                            content=msg.content,
                            source=msg.source,
                            metadata=msg.metadata,
                            parts=SessionRunner._degrade_image_parts(msg.parts),
                            reasoning=msg.reasoning,
                            reasoning_signature=msg.reasoning_signature,
                            has_redacted_thinking=msg.has_redacted_thinking,
                        )
                    )
                else:
                    result.append(msg)
            return result

        return messages

    @staticmethod
    def _context_protocol_summary(
        messages: list[ContextMessage],
    ) -> dict[str, Any]:
        assistant_tool_call_ids: list[list[str]] = []
        tool_call_ids: list[str] = []
        tool_messages_missing_ids = 0

        for message in messages:
            if message.role == "assistant" and isinstance(message.metadata, dict):
                raw_tool_calls = message.metadata.get("tool_calls")
                if isinstance(raw_tool_calls, list):
                    ids: list[str] = []
                    for item in raw_tool_calls:
                        if not isinstance(item, dict):
                            continue
                        call_id = item.get("id")
                        if isinstance(call_id, str):
                            ids.append(call_id)
                    if ids:
                        assistant_tool_call_ids.append(ids)
            elif message.role == "tool":
                call_id = (
                    message.metadata.get("tool_call_id")
                    if isinstance(message.metadata, dict)
                    else None
                )
                if isinstance(call_id, str) and call_id:
                    tool_call_ids.append(call_id)
                else:
                    tool_messages_missing_ids += 1

        return {
            "roles": [message.role for message in messages],
            "sources": [message.source for message in messages],
            "assistant_tool_call_ids": assistant_tool_call_ids,
            "tool_call_ids": tool_call_ids,
            "tool_messages_missing_ids": tool_messages_missing_ids,
        }

    @staticmethod
    def _degrade_image_parts(parts: list[ContextPart]) -> list[ContextPart]:
        """Convert image_url / image_base64 parts to image_description."""
        degraded: list[ContextPart] = []
        for part in parts:
            if part.type in ("image_url", "image_base64"):
                desc = part.text or f"[Image: {part.media_id or 'unknown'}]"
                degraded.append(
                    ContextPart(
                        type="image_description",
                        text=desc,
                        media_id=part.media_id,
                        mime_type=part.mime_type,
                    )
                )
            else:
                degraded.append(part)
        return degraded

    def _collect_tools(
        self,
        tool_filter: AbstractSet[str] | None,
        *,
        tool_allowlist: AbstractSet[str] | None = None,
        capabilities: ModelCapabilities | None = None,
    ) -> list[ToolDefinition]:
        tools: list[ToolDefinition] = []
        denied = set(tool_filter or ())
        allowed = set(tool_allowlist or ())
        if self._tools is not None:
            tools.extend(
                ToolDefinition(
                    name=entry.name,
                    description=entry.description,
                    parameters=entry.parameters,
                )
                for entry in self._tools.all()
                if entry.name not in denied and (not allowed or entry.name in allowed)
            )

        # Conditionally inject image_understand tool for non-vision models
        if (
            capabilities is not None
            and not capabilities.image_input
            and self._multimodal_config is not None
            and self._multimodal_config.image_fallback_mode == "tool"
            and "image_understand" not in denied
            and (not allowed or "image_understand" in allowed)
            and "image_understand" not in {tool.name for tool in tools}
        ):
            tools.append(
                ToolDefinition(
                    name="image_understand",
                    description=(
                        "Analyze an image attached to the current conversation. "
                        "Returns a detailed description, any visible text (OCR), "
                        "and safety observations."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "media_id": {
                                "type": "string",
                                "description": (
                                    "The media ID of the image to analyze. "
                                    "Use 'latest' for the most recently attached image."
                                ),
                            },
                            "question": {
                                "type": "string",
                                "description": "Optional specific question about the image.",
                            },
                        },
                        "required": ["media_id"],
                        "additionalProperties": False,
                    },
                )
            )

        return tools

    async def _build_user_parts(
        self,
        user_message: str,
        attachments: list[InboundAttachment],
        *,
        capabilities: ModelCapabilities | None,
    ) -> list[ContextPart]:
        """Build provider context parts from user message and attachments."""
        image_input = bool(capabilities and capabilities.image_input)
        max_count = capabilities.max_image_count if capabilities else 0
        max_bytes = capabilities.max_image_bytes if capabilities else 0
        supported = capabilities.supported_image_mime_types if capabilities else ()
        image_count = sum(1 for att in attachments if att.kind == "image")

        if image_input:
            logger.debug(
                "session_runner.multimodal_route",
                route="native_vision",
                image_count=image_count,
                max_image_count=max_count,
                max_image_bytes=max_bytes,
                supported_image_mime_types=supported,
            )
            return await self._build_vision_parts(
                user_message,
                attachments,
                max_count=max_count,
                max_bytes=max_bytes,
                supported=supported,
            )

        # Non-vision model: apply fallback mode
        if not any(att.kind == "image" for att in attachments):
            # No image attachments, return empty — text goes through user_message
            logger.debug(
                "session_runner.multimodal_route",
                route="text_only",
                image_count=0,
            )
            return []

        logger.debug(
            "session_runner.multimodal_route",
            route="fallback",
            image_count=image_count,
            image_fallback_mode=(
                self._multimodal_config.image_fallback_mode
                if self._multimodal_config is not None
                else ""
            ),
        )
        return await self._build_fallback_parts(user_message, attachments)

    async def _build_vision_parts(
        self,
        user_message: str,
        attachments: list[InboundAttachment],
        *,
        max_count: int,
        max_bytes: int,
        supported: tuple[str, ...],
    ) -> list[ContextPart]:
        """Build parts for a vision-capable model."""
        parts: list[ContextPart] = []
        if user_message:
            parts.append(ContextPart(type="text", text=user_message))

        image_attachments = [att for att in attachments if att.kind == "image"]
        if max_count > 0:
            image_attachments = image_attachments[:max_count]

        supported_set = set(supported)
        for attachment in image_attachments:
            if (
                attachment.mime_type
                and supported_set
                and attachment.mime_type not in supported_set
            ):
                logger.debug(
                    "session_runner.image_skipped",
                    reason="unsupported_mime_type",
                    media_id=attachment.platform_id,
                    mime_type=attachment.mime_type,
                )
                continue

            resolved = await self._resolve_attachment(attachment)
            if max_bytes > 0 and resolved.file_size > max_bytes:
                logger.debug(
                    "session_runner.image_skipped",
                    reason="too_large",
                    media_id=attachment.platform_id,
                    file_size=resolved.file_size,
                    max_image_bytes=max_bytes,
                )
                if attachment.alt_text:
                    parts.append(
                        ContextPart(
                            type="image_description",
                            text=attachment.alt_text,
                            media_id=attachment.platform_id,
                            mime_type=attachment.mime_type,
                        )
                    )
                continue
            if resolved.base64_data:
                logger.debug(
                    "session_runner.image_part_built",
                    media_id=attachment.platform_id,
                    part_type="image_base64",
                    source=resolved.source,
                    mime_type=resolved.mime_type,
                    file_size=resolved.file_size,
                )
                parts.append(
                    ContextPart(
                        type="image_base64",
                        data=resolved.base64_data,
                        mime_type=resolved.mime_type,
                        media_id=resolved.media_id,
                    )
                )
            elif resolved.local_path and attachment.url:
                logger.debug(
                    "session_runner.image_part_built",
                    media_id=attachment.platform_id,
                    part_type="image_url",
                    source=resolved.source,
                    mime_type=resolved.mime_type,
                    file_size=resolved.file_size,
                )
                parts.append(
                    ContextPart(
                        type="image_url",
                        url=attachment.url,
                        media_id=resolved.media_id,
                        mime_type=resolved.mime_type,
                    )
                )
            elif attachment.url:
                logger.debug(
                    "session_runner.image_part_built",
                    media_id=attachment.platform_id,
                    part_type="image_url",
                    source="attachment_url",
                    mime_type=attachment.mime_type,
                    file_size=attachment.file_size,
                )
                parts.append(
                    ContextPart(
                        type="image_url",
                        url=attachment.url,
                        media_id=attachment.platform_id,
                        mime_type=attachment.mime_type,
                    )
                )
            elif attachment.alt_text:
                logger.debug(
                    "session_runner.image_part_built",
                    media_id=attachment.platform_id,
                    part_type="image_description",
                    source="alt_text",
                )
                parts.append(
                    ContextPart(
                        type="image_description",
                        text=attachment.alt_text,
                        media_id=attachment.platform_id,
                    )
                )

        return parts

    async def _build_fallback_parts(
        self,
        user_message: str,
        attachments: list[InboundAttachment],
    ) -> list[ContextPart]:
        """Build parts for a non-vision model using fallback mode."""
        if self._multimodal_config is None:
            return []

        mode = self._multimodal_config.image_fallback_mode
        image_attachments = [att for att in attachments if att.kind == "image"]

        if mode == "off" or not image_attachments:
            logger.debug(
                "session_runner.fallback_parts_skipped",
                reason="mode_off_or_no_images",
                image_fallback_mode=mode,
                image_count=len(image_attachments),
            )
            return []

        parts: list[ContextPart] = []
        if user_message:
            parts.append(ContextPart(type="text", text=user_message))

        if mode == "auto":
            logger.debug(
                "session_runner.fallback_auto_describe",
                image_count=min(len(image_attachments), 4),
                fallback_provider=self._multimodal_config.image_fallback_provider,
                fallback_model=self._multimodal_config.image_fallback_model,
            )
            for att in image_attachments[:4]:
                description = await self._auto_describe_image(att)
                parts.append(
                    ContextPart(
                        type="image_description",
                        text=description,
                        media_id=att.platform_id,
                        mime_type=att.mime_type,
                    )
                )

        elif mode == "tool":
            logger.debug(
                "session_runner.fallback_tool_hint",
                image_count=min(len(image_attachments), 4),
            )
            for att in image_attachments[:4]:
                desc = (
                    att.alt_text
                    or f"[Image attached: {att.platform_id}. Use image_understand tool to analyze it.]"
                )
                parts.append(
                    ContextPart(
                        type="image_description",
                        text=desc,
                        media_id=att.platform_id,
                        mime_type=att.mime_type,
                    )
                )

        return parts

    async def _auto_describe_image(self, attachment: InboundAttachment) -> str:
        """Call fallback vision provider to generate an image description."""
        if self._providers is None or self._multimodal_config is None:
            return attachment.alt_text or f"[Image: {attachment.platform_id}]"

        routed = self._resolve_task_model(
            "image_fallback",
            explicit=_legacy_model_spec(
                provider_id=self._multimodal_config.image_fallback_provider,
                model=self._multimodal_config.image_fallback_model,
            ),
            default_spec="vision",
            fallback="disabled",
            legacy_provider_id=self._multimodal_config.image_fallback_provider,
        )
        if routed is None:
            logger.debug(
                "session_runner.fallback_vision_skipped",
                reason="missing_fallback_model",
                media_id=attachment.platform_id,
            )
            return attachment.alt_text or f"[Image: {attachment.platform_id}]"
        slot, fallback_model, route_reason = routed

        resolved = await self._resolve_attachment(attachment)

        content_parts: list[ContextPart] = [
            ContextPart(type="text", text=_FALLBACK_VISION_PROMPT),
        ]
        if resolved.base64_data:
            content_parts.append(
                ContextPart(
                    type="image_base64",
                    data=resolved.base64_data,
                    mime_type=resolved.mime_type,
                    media_id=resolved.media_id,
                )
            )
        elif attachment.url:
            content_parts.append(
                ContextPart(
                    type="image_url",
                    url=attachment.url,
                    media_id=attachment.platform_id,
                    mime_type=attachment.mime_type,
                )
            )
        else:
            logger.debug(
                "session_runner.fallback_vision_skipped",
                reason="image_not_resolved",
                media_id=attachment.platform_id,
                resolved_source=resolved.source,
            )
            return attachment.alt_text or f"[Image: {attachment.platform_id}]"

        vision_msg = ContextMessage(
            role="user",
            source="auto_fallback",
            content=_FALLBACK_VISION_PROMPT,
            parts=content_parts,
        )

        chat_kwargs: dict[str, Any] = {}
        if fallback_model:
            chat_kwargs["model"] = fallback_model

        try:
            logger.debug(
                "session_runner.fallback_vision_call",
                media_id=attachment.platform_id,
                fallback_provider=slot.id,
                fallback_model=fallback_model or slot.default_model,
                route_reason=route_reason,
                image_part_type=content_parts[-1].type,
            )
            response = await slot.provider.chat(messages=[vision_msg], **chat_kwargs)
            if response.content:
                logger.debug(
                    "session_runner.fallback_vision_success",
                    media_id=attachment.platform_id,
                    fallback_provider=slot.id,
                    fallback_model=fallback_model or slot.default_model,
                    description_chars=len(response.content),
                )
                return response.content
        except Exception as exc:
            logger.warning(
                "session_runner.fallback_vision_failed",
                media_id=attachment.platform_id,
                error=str(exc),
            )

        return attachment.alt_text or f"[Image: {attachment.platform_id}]"

    async def _resolve_attachment(self, attachment: InboundAttachment) -> Any:
        """Resolve an attachment via MediaResolver if available."""
        attachment = await self._download_platform_attachment_if_needed(attachment)
        if self._media_resolver is None:
            from nahida_bot.agent.media.resolver import ResolvedMedia

            return ResolvedMedia(
                media_id=attachment.platform_id,
                mime_type=attachment.mime_type,
                local_path=attachment.path,
                file_size=attachment.file_size,
                width=attachment.width,
                height=attachment.height,
                description=attachment.alt_text,
            )
        return await self._media_resolver.resolve(attachment)

    async def _download_platform_attachment_if_needed(
        self, attachment: InboundAttachment
    ) -> InboundAttachment:
        """Use the current channel service to materialize opaque platform media IDs."""
        if attachment.path or attachment.url or not attachment.platform_id:
            logger.debug(
                "session_runner.platform_media_download_skipped",
                reason=(
                    "already_resolved"
                    if attachment.path or attachment.url
                    else "missing_platform_id"
                ),
                media_id=attachment.platform_id,
            )
            return attachment
        if self._channel_registry is None:
            logger.debug(
                "session_runner.platform_media_download_skipped",
                reason="no_channel_registry",
                media_id=attachment.platform_id,
            )
            return attachment
        ctx = current_session.get()
        if ctx is None:
            logger.debug(
                "session_runner.platform_media_download_skipped",
                reason="no_session_context",
                media_id=attachment.platform_id,
            )
            return attachment
        channel = self._channel_registry.get(ctx.platform)
        if channel is None:
            logger.debug(
                "session_runner.platform_media_download_skipped",
                reason="channel_not_found",
                platform=ctx.platform,
                media_id=attachment.platform_id,
            )
            return attachment
        download = getattr(channel, "download_media", None)
        if download is None:
            logger.debug(
                "session_runner.platform_media_download_skipped",
                reason="download_media_unavailable",
                platform=ctx.platform,
                media_id=attachment.platform_id,
            )
            return attachment

        try:
            logger.debug(
                "session_runner.platform_media_download_start",
                platform=ctx.platform,
                media_id=attachment.platform_id,
            )
            result = await download(attachment.platform_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "session_runner.platform_media_download_failed",
                platform=ctx.platform,
                media_id=attachment.platform_id,
                error=str(exc),
            )
            return attachment

        if result is None or not getattr(result, "path", ""):
            logger.debug(
                "session_runner.platform_media_download_empty",
                platform=ctx.platform,
                media_id=attachment.platform_id,
            )
            return attachment
        logger.debug(
            "session_runner.platform_media_download_success",
            platform=ctx.platform,
            media_id=attachment.platform_id,
            mime_type=result.mime_type or attachment.mime_type,
            file_size=result.file_size or attachment.file_size,
        )
        return replace(
            attachment,
            path=result.path,
            mime_type=result.mime_type or attachment.mime_type,
            file_size=result.file_size or attachment.file_size,
        )

    async def _find_attachment_in_history(
        self, media_id: str
    ) -> InboundAttachment | None:
        """Search recent session history for an attachment matching media_id."""
        current_images = [
            att for att in current_attachments.get() if att.kind == "image"
        ]
        if media_id == "latest" and current_images:
            return current_images[-1]
        for attachment in current_images:
            if attachment.platform_id == media_id:
                return attachment

        if self._memory is None:
            return None
        ctx = current_session.get()
        if ctx is None:
            return None

        records = await self._memory.get_recent(
            ctx.session_id, limit=self._max_history_turns
        )
        for record in reversed(records):
            if record.turn.role != "user":
                continue
            attachments = self._attachments_from_metadata(record.turn.metadata)
            if media_id == "latest" and attachments:
                return attachments[-1]
            for attachment in attachments:
                if attachment.platform_id == media_id:
                    return attachment
        return None

    def _build_system_prompt(
        self,
        system_prompt: str,
        context: MessageContext | None,
        source_tag: str = "user_input",
        agent_instruction: str = "",
        enable_silent_reply: bool = True,
    ) -> str:
        parts = [system_prompt.rstrip()]
        if context is not None and context.channel not in ("", "bot"):
            parts.append(ENVELOPE_INSTRUCTION)
        if enable_silent_reply:
            parts.append(SILENT_REPLY_INSTRUCTION)
        if source_tag == "cron_trigger":
            parts.append(HEARTBEAT_INSTRUCTION)
        if source_tag == "proactive_join":
            parts.append(PROACTIVE_JOIN_INSTRUCTION)
            instruction = agent_instruction.strip()
            if instruction:
                parts.append(
                    "## Conversation Joiner Instruction\n"
                    "The joiner supplied this non-user instruction for the current "
                    f"run:\n{instruction}"
                )
        if self._supplement_registry is not None and context is not None:
            supplements = self._supplement_registry.get_matching(context)
            parts.extend(supplements)
        return "\n\n".join(parts)

    def _resolve_workspace_root(self, workspace_id: str | None) -> Any:
        if self._workspace is None or workspace_id is None:
            return None
        return self._workspace.workspace_path(workspace_id)

    async def _build_user_turn_metadata(
        self,
        *,
        attachments: list[InboundAttachment],
        image_descriptions: dict[str, str] | None = None,
        message_context: MessageContext | None,
    ) -> dict[str, Any] | None:
        metadata: dict[str, Any] | None = None
        message_context_metadata = message_context_to_metadata(message_context)
        if message_context_metadata is not None:
            metadata = {"message_context": message_context_metadata}
        if attachments:
            persisted_attachments: list[dict[str, Any]] = []
            for att in attachments:
                persisted = {
                    "kind": att.kind,
                    "platform_id": att.platform_id,
                    "url": "",
                    "path": att.path,
                    "mime_type": att.mime_type,
                    "file_size": att.file_size,
                    "width": att.width,
                    "height": att.height,
                    "alt_text": att.alt_text,
                    "metadata": att.metadata,
                }
                if att.kind == "image":
                    generated_description = (image_descriptions or {}).get(
                        att.platform_id, ""
                    )
                    resolved = await self._resolve_attachment(att)
                    persisted.update(
                        {
                            "path": resolved.local_path or att.path,
                            "mime_type": resolved.mime_type or att.mime_type,
                            "file_size": resolved.file_size or att.file_size,
                            "width": resolved.width or att.width,
                            "height": resolved.height or att.height,
                            "description": (
                                generated_description
                                or resolved.description
                                or att.alt_text
                            ),
                        }
                    )
                persisted_attachments.append(persisted)
            if metadata is None:
                metadata = {}
            metadata["attachments"] = persisted_attachments
        return metadata

    @staticmethod
    def _image_descriptions_from_parts(parts: list[ContextPart]) -> dict[str, str]:
        """Extract stable generated image descriptions from current-turn parts."""
        descriptions: dict[str, str] = {}
        for part in parts:
            if part.type != "image_description" or not part.media_id or not part.text:
                continue
            descriptions[part.media_id] = part.text
        return descriptions

    def _assistant_visible_turns(
        self,
        result: Any,
        *,
        include_message_context: bool,
    ) -> list[ConversationTurn]:
        """Project loop output to cache-friendly visible assistant history.

        Tool-call metadata is intentionally not persisted here. A visible
        assistant answer that also requested tools should be replayed as normal
        natural-language history, not as an unfinished provider tool transcript.
        """
        raw_messages = getattr(result, "assistant_messages", None)
        assistant_messages = raw_messages if isinstance(raw_messages, list) else []
        visible: list[tuple[str, Any | None]] = []
        seen: set[str] = set()

        for message in assistant_messages:
            content = strip_envelope_prefix(str(getattr(message, "content", "") or ""))
            if not content or content in seen:
                continue
            visible.append((content, message))
            seen.add(content)

        final_response = strip_envelope_prefix(
            str(getattr(result, "final_response", "") or "")
        )
        fallback_metadata_source = (
            assistant_messages[-1] if assistant_messages else None
        )
        if final_response and final_response not in seen:
            visible.append((final_response, fallback_metadata_source))

        if not visible:
            return []

        assistant_context_metadata = message_context_to_metadata(
            assistant_context() if include_message_context else None
        )
        last_index = len(visible) - 1
        turns: list[ConversationTurn] = []
        for index, (content, source_message) in enumerate(visible):
            turns.append(
                ConversationTurn(
                    role="assistant",
                    content=content,
                    source="agent_response",
                    metadata=self._assistant_turn_metadata(
                        source_message,
                        message_context_metadata=(
                            assistant_context_metadata if index == last_index else None
                        ),
                    ),
                )
            )
        return turns

    @staticmethod
    def _assistant_turn_metadata(
        message: Any | None,
        *,
        message_context_metadata: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        metadata: dict[str, Any] = {}
        if message is not None:
            reasoning = getattr(message, "reasoning", None)
            if isinstance(reasoning, str) and reasoning:
                metadata["reasoning"] = reasoning
            reasoning_signature = getattr(message, "reasoning_signature", None)
            if isinstance(reasoning_signature, str) and reasoning_signature:
                metadata["reasoning_signature"] = reasoning_signature
            if getattr(message, "has_redacted_thinking", False) is True:
                metadata["has_redacted_thinking"] = True
        if message_context_metadata is not None:
            metadata["message_context"] = message_context_metadata
        return metadata or None

    async def persist_observed_message(
        self,
        *,
        inbound: InboundMessage,
        session_id: str,
        workspace_id: str | None = None,
    ) -> None:
        """Persist a group message as context without running the agent."""
        if self._memory is None:
            logger.debug(
                "session_runner.observed_persist_skipped",
                reason="no_memory_store",
                session_id=session_id,
                platform=inbound.platform,
                chat_id=inbound.chat_id,
                message_id=inbound.message_id,
            )
            return
        logger.debug(
            "session_runner.observed_persist_start",
            session_id=session_id,
            workspace_id=workspace_id or "",
            platform=inbound.platform,
            chat_id=inbound.chat_id,
            message_id=inbound.message_id,
            text_chars=len(inbound.text),
            mentions_bot=inbound.mentions_bot,
            mentioned_user_ids=list(inbound.mentioned_user_ids),
        )
        await self._memory.ensure_session(session_id, workspace_id=workspace_id)
        metadata = await self._build_user_turn_metadata(
            attachments=inbound.attachments,
            image_descriptions=None,
            message_context=inbound.message_context or context_from_inbound(inbound),
        )
        if metadata is None:
            metadata = {}
        metadata["observed_only"] = True
        metadata["triggered_agent"] = False
        metadata["mentions_bot"] = inbound.mentions_bot
        if inbound.mentioned_user_ids:
            metadata["mentioned_user_ids"] = list(inbound.mentioned_user_ids)

        await self._memory.append_turn(
            session_id,
            ConversationTurn(
                role="user",
                content=inbound.text,
                source="group_observation",
                metadata=metadata,
            ),
        )
        logger.debug(
            "session_runner.observed_persist_done",
            session_id=session_id,
            workspace_id=workspace_id or "",
            platform=inbound.platform,
            chat_id=inbound.chat_id,
            message_id=inbound.message_id,
        )

    async def _persist_turns(
        self,
        session_id: str,
        user_message: str,
        result: Any,
        *,
        attachments: list[InboundAttachment],
        image_descriptions: dict[str, str] | None = None,
        message_context: MessageContext | None = None,
        source_tag: str,
        workspace_id: str | None = None,
        workspace_root: Any = None,
    ) -> None:
        if self._memory is None:
            logger.debug(
                "session_runner.persist_turns_skipped",
                reason="no_memory_store",
                session_id=session_id,
                source_tag=source_tag,
            )
            return
        logger.debug(
            "session_runner.persist_turns_start",
            session_id=session_id,
            source_tag=source_tag,
            workspace_id=workspace_id or "",
            user_message_chars=len(user_message),
            attachment_count=len(attachments),
            attachment_kinds=[att.kind for att in attachments],
            final_response_chars=len(str(getattr(result, "final_response", "") or "")),
            **_message_context_log_fields(message_context),
        )
        metadata = await self._build_user_turn_metadata(
            attachments=attachments,
            image_descriptions=image_descriptions,
            message_context=message_context,
        )
        user_turn = ConversationTurn(
            role="user", content=user_message, source=source_tag, metadata=metadata
        )
        await self._memory.append_turn(session_id, user_turn)

        assistant_turns = self._assistant_visible_turns(
            result,
            include_message_context=message_context is not None,
        )
        if assistant_turns:
            assistant_messages = getattr(result, "assistant_messages", None)
            tool_messages = getattr(result, "tool_messages", None)
            final_response = str(getattr(result, "final_response", "") or "")
            logger.debug(
                "session_runner.persist_agent_result",
                session_id=session_id,
                final_response_chars=len(final_response),
                final_response_preview=final_response[:200],
                assistant_message_count=(
                    len(assistant_messages)
                    if isinstance(assistant_messages, list)
                    else 0
                ),
                persisted_assistant_turn_count=len(assistant_turns),
                tool_message_count=(
                    len(tool_messages) if isinstance(tool_messages, list) else 0
                ),
                assistant_sources=[
                    getattr(message, "source", "")
                    for message in assistant_messages[:10]
                ]
                if isinstance(assistant_messages, list)
                else [],
                assistant_metadata_keys=[
                    sorted(message.metadata.keys())
                    if getattr(message, "metadata", None)
                    else []
                    for message in assistant_messages[:10]
                ]
                if isinstance(assistant_messages, list)
                else [],
                tool_sources=[
                    getattr(message, "source", "") for message in tool_messages[:10]
                ]
                if isinstance(tool_messages, list)
                else [],
            )
            for assistant_turn in assistant_turns:
                await self._memory.append_turn(session_id, assistant_turn)

        await self._consolidate_memory_after_turn(
            session_id=session_id,
            user_message=user_message,
            assistant_message="\n\n".join(turn.content for turn in assistant_turns),
            workspace_id=workspace_id,
            workspace_root=workspace_root,
        )
        logger.debug(
            "session_runner.persist_turns_done",
            session_id=session_id,
            source_tag=source_tag,
            workspace_id=workspace_id or "",
            persisted_assistant_turn_count=len(assistant_turns),
        )

    async def _consolidate_memory_after_turn(
        self,
        *,
        session_id: str,
        user_message: str,
        assistant_message: str,
        workspace_id: str | None,
        workspace_root: Any,
    ) -> None:
        """Run non-blocking-looking memory consolidation on the completed turn."""
        if self._memory_consolidator is None:
            return
        resolved_root = workspace_root
        if resolved_root is None and workspace_id is not None:
            resolved_root = self._resolve_workspace_root(workspace_id)
        try:
            scope_type, scope_id = resolve_scope_from_session(session_id)
            # Identity for identity-aware write scope (issue #7, Phase 3).
            # Empty when identity is off / unlinked / context unset → V1.
            ctx = current_session.get()
            person_id = ctx.person_id if ctx is not None else None
            sender_account_key = ctx.sender_account_key if ctx is not None else ""
            applied = await self._memory_consolidator.consolidate_turn(
                session_id=session_id,
                user_message=user_message,
                assistant_message=assistant_message,
                workspace_id=workspace_id,
                workspace_root=resolved_root,
                run_rules=self._memory_consolidation_rule_based_enabled,
                scope_type=scope_type,
                scope_id=scope_id,
                person_id=person_id,
                sender_account_key=sender_account_key,
            )
            if applied:
                logger.debug(
                    "session_runner.memory_consolidated",
                    session_id=session_id,
                    workspace_id=workspace_id or "",
                    applied=applied,
                )
                await self._embed_memory_items_after_consolidation()
        except Exception as exc:
            logger.warning(
                "session_runner.memory_consolidation_failed",
                session_id=session_id,
                error=str(exc),
            )

    async def _embed_memory_items_after_consolidation(self) -> None:
        """Refresh embeddings after durable memory changes when configured."""
        if (
            not self._memory_embed_after_consolidation
            or self._memory is None
            or self._memory_embedding_provider is None
        ):
            return
        embed_items = getattr(self._memory, "embed_items_all_scopes", None) or getattr(
            self._memory, "embed_items", None
        )
        if not callable(embed_items):
            return
        try:
            count = await cast(Any, embed_items)(
                self._memory_embedding_provider,
                vector_index=self._memory_vector_index,
            )
            logger.debug("session_runner.memory_embeddings_refreshed", count=count)
        except Exception as exc:
            logger.warning("session_runner.memory_embedding_failed", error=str(exc))


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _legacy_model_spec(*, provider_id: str = "", model: str = "") -> str:
    """Build a model spec from legacy provider/model split fields."""
    provider_id = provider_id.strip()
    model = model.strip()
    if provider_id and model:
        if model.startswith(f"{provider_id}/"):
            return model
        return f"{provider_id}/{model}"
    return model


def _message_context_log_fields(message_context: Any | None) -> dict[str, object]:
    if message_context is None:
        return {
            "context_channel": "",
            "context_chat_type": "",
            "context_chat_id": "",
            "context_sender_id": "",
            "context_sender_roles": [],
            "has_message_context": False,
        }
    return {
        "context_channel": getattr(message_context, "channel", ""),
        "context_chat_type": getattr(message_context, "chat_type", ""),
        "context_chat_id": getattr(message_context, "chat_id", ""),
        "context_sender_id": getattr(message_context, "sender_id", ""),
        "context_sender_roles": list(
            getattr(message_context, "sender_role_tags", ()) or ()
        ),
        "has_message_context": True,
    }
