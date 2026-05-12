"""Shared agent execution pipeline for message dispatch and cron fires."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, AbstractSet, Any, cast

import structlog

from nahida_bot.agent.context import ContextMessage, ContextPart
from nahida_bot.agent.loop import AgentRunResult
from nahida_bot.agent.memory.consolidation import MemoryConsolidator
from nahida_bot.agent.memory.sqlite import build_fts_query
from nahida_bot.agent.memory.models import ConversationTurn
from nahida_bot.agent.providers import ToolDefinition
from nahida_bot.core.config import MediaContextPolicy
from nahida_bot.core.context import current_attachments, current_session
from nahida_bot.core.logging import log_trace
from nahida_bot.core.message_context import (
    assistant_context,
    message_context_from_metadata,
    message_context_to_metadata,
    render_message_with_context,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from nahida_bot.agent.loop import AgentLoop, LoopEvent
    from nahida_bot.agent.media.resolver import MediaResolver
    from nahida_bot.agent.memory.store import MemoryStore
    from nahida_bot.agent.providers.base import ModelCapabilities
    from nahida_bot.agent.providers.manager import ProviderManager
    from nahida_bot.core.channel_registry import ChannelRegistry
    from nahida_bot.core.config import MultimodalConfig
    from nahida_bot.plugins.base import InboundAttachment, MessageContext
    from nahida_bot.plugins.registry import ToolRegistry
    from nahida_bot.workspace.manager import WorkspaceManager

logger = structlog.get_logger(__name__)

_FALLBACK_VISION_PROMPT = (
    "Describe this image in detail. Include any visible text (OCR). "
    "Note any safety concerns."
)


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
        multimodal_config: MultimodalConfig | None = None,
        media_resolver: MediaResolver | None = None,
        channel_registry: ChannelRegistry | None = None,
    ) -> None:
        self._agent = agent_loop
        self._memory = memory_store
        self._memory_consolidator = (
            MemoryConsolidator(memory_store) if memory_store is not None else None
        )
        self._providers = provider_manager
        self._workspace = workspace_manager
        self._tools = tool_registry
        self._max_history_turns = max_history_turns
        self._multimodal_config = multimodal_config
        self._media_resolver = media_resolver
        self._channel_registry = channel_registry

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
        tool_filter: AbstractSet[str] | None = None,
        source_tag: str = "user_input",
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
            tool_filter=tool_filter,
            source_tag=source_tag,
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
        tool_filter: AbstractSet[str] | None = None,
        source_tag: str = "user_input",
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
        done_data: dict[str, Any] = {}
        try:
            provider_slot, selected_model = await self._resolve_provider(session_id)
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
            image_count = sum(1 for att in attachments_for_turn if att.kind == "image")
            logger.debug(
                "session_runner.route_selected",
                session_id=session_id,
                provider_id=provider_slot.id if provider_slot is not None else "",
                selected_model=selected_model or "",
                effective_model=effective_model,
                image_input=bool(capabilities and capabilities.image_input),
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

            history = await self._load_history(
                session_id,
                workspace_id=workspace_id,
                capabilities=capabilities,
            )
            relevant_memory = await self._load_relevant_memory(user_message)
            if relevant_memory:
                history = [relevant_memory, *history]
            tools = self._collect_tools(tool_filter, capabilities=capabilities)
            logger.debug(
                "session_runner.tools_collected",
                session_id=session_id,
                provider_id=provider_slot.id if provider_slot is not None else "",
                effective_model=effective_model,
                tool_count=len(tools),
                tool_names=[tool.name for tool in tools[:50]],
                tool_filter=sorted(tool_filter) if tool_filter is not None else [],
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

            run_kwargs: dict[str, Any] = {
                "user_message": visible_user_message,
                "system_prompt": system_prompt,
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
                run_kwargs["context_builder"] = provider_slot.context_builder
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
            async for event in self._agent.run_stream(**run_kwargs):
                if event.type == "done":
                    done_data = {
                        "final_response": event.final_response or "",
                        "assistant_messages": list(event.assistant_messages or []),
                        "tool_messages": list(event.tool_messages or []),
                        "steps": event.steps,
                        "trace_id": event.trace_id,
                        "error": event.error,
                    }
                yield event
            logger.debug(
                "session_runner.agent_run_done",
                session_id=session_id,
                trace_id=done_data.get("trace_id"),
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
                message_context=message_context,
                source_tag=source_tag,
                workspace_id=workspace_id,
                workspace_root=workspace_root,
            )
        finally:
            current_attachments.reset(attachments_token)

    # ── Public helpers (used by image_understand tool) ─────────

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

        fallback_provider_id = self._multimodal_config.image_fallback_provider
        fallback_model = self._multimodal_config.image_fallback_model
        if not fallback_provider_id:
            return "Error: no fallback vision provider configured"

        slot = self._providers.get(fallback_provider_id)
        if slot is None:
            return f"Error: fallback provider '{fallback_provider_id}' not found"

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

    async def _resolve_provider(self, session_id: str) -> tuple[Any, str | None]:
        if self._providers is None:
            logger.debug(
                "session_runner.provider_resolved",
                session_id=session_id,
                reason="no_provider_manager",
            )
            return None, None
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
    ) -> list[ContextMessage]:
        if self._memory is None:
            logger.debug(
                "session_runner.history_skipped",
                session_id=session_id,
                reason="no_memory_store",
            )
            return []
        await self._memory.ensure_session(session_id, workspace_id=workspace_id)
        records = await self._memory.get_recent(
            session_id, limit=self._max_history_turns
        )
        logger.debug(
            "session_runner.history_loaded",
            session_id=session_id,
            workspace_id=workspace_id or "",
            record_count=len(records),
            max_history_turns=self._max_history_turns,
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

        messages: list[ContextMessage] = []
        for r in records:
            metadata = r.turn.metadata
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

    async def _load_relevant_memory(self, query: str) -> ContextMessage | None:
        """Load a small relevant durable-memory context block for the current turn."""
        if self._memory is None or not query.strip() or not build_fts_query(query):
            return None
        search_items = getattr(self._memory, "search_items", None)
        if not callable(search_items):
            return None
        try:
            items = await cast(Any, search_items)(query, limit=5)
        except Exception as exc:
            logger.warning("session_runner.memory_search_failed", error=str(exc))
            return None
        if not items:
            return None

        lines = [
            "Relevant durable memory:",
            "Treat memory as helpful context, not unquestionable truth. Current user instructions and current files take precedence.",
        ]
        remaining = 1200
        for item in items:
            kind = getattr(item, "kind", "memory")
            title = getattr(item, "title", "")
            content = str(getattr(item, "content", "")).strip()
            item_id = getattr(item, "item_id", "")
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
        return ContextMessage(
            role="system",
            source="long_term_memory",
            content="\n".join(lines),
            metadata={"memory_backend": "items", "memory_count": len(lines) - 2},
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
        capabilities: ModelCapabilities | None = None,
    ) -> list[ToolDefinition]:
        tools: list[ToolDefinition] = []
        if self._tools is not None:
            tools.extend(
                ToolDefinition(
                    name=entry.name,
                    description=entry.description,
                    parameters=entry.parameters,
                )
                for entry in self._tools.all()
                if tool_filter is None or entry.name not in tool_filter
            )

        # Conditionally inject image_understand tool for non-vision models
        if (
            capabilities is not None
            and not capabilities.image_input
            and self._multimodal_config is not None
            and self._multimodal_config.image_fallback_mode == "tool"
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

        provider_id = self._multimodal_config.image_fallback_provider
        fallback_model = self._multimodal_config.image_fallback_model
        if not provider_id:
            logger.debug(
                "session_runner.fallback_vision_skipped",
                reason="missing_fallback_provider",
                media_id=attachment.platform_id,
            )
            return attachment.alt_text or f"[Image: {attachment.platform_id}]"

        slot = self._providers.get(provider_id)
        if slot is None:
            logger.debug(
                "session_runner.fallback_vision_skipped",
                reason="fallback_provider_not_found",
                media_id=attachment.platform_id,
                fallback_provider=provider_id,
            )
            return attachment.alt_text or f"[Image: {attachment.platform_id}]"

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
                fallback_provider=provider_id,
                fallback_model=fallback_model or slot.default_model,
                image_part_type=content_parts[-1].type,
            )
            response = await slot.provider.chat(messages=[vision_msg], **chat_kwargs)
            if response.content:
                logger.debug(
                    "session_runner.fallback_vision_success",
                    media_id=attachment.platform_id,
                    fallback_provider=provider_id,
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
        attachments: list[InboundAttachment],
        message_context: MessageContext | None = None,
        source_tag: str,
        workspace_id: str | None = None,
        workspace_root: Any = None,
    ) -> None:
        if self._memory is None:
            return
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
                    resolved = await self._resolve_attachment(att)
                    persisted.update(
                        {
                            "path": resolved.local_path or att.path,
                            "mime_type": resolved.mime_type or att.mime_type,
                            "file_size": resolved.file_size or att.file_size,
                            "width": resolved.width or att.width,
                            "height": resolved.height or att.height,
                            "description": resolved.description or att.alt_text,
                        }
                    )
                persisted_attachments.append(persisted)
            if metadata is None:
                metadata = {}
            metadata["attachments"] = persisted_attachments
        user_turn = ConversationTurn(
            role="user", content=user_message, source=source_tag, metadata=metadata
        )
        await self._memory.append_turn(session_id, user_turn)

        # Persist assistant turn with reasoning metadata
        if result.final_response:
            assistant_metadata: dict[str, Any] | None = None
            assistant_messages = getattr(result, "assistant_messages", None)
            tool_messages = getattr(result, "tool_messages", None)
            logger.debug(
                "session_runner.persist_agent_result",
                session_id=session_id,
                final_response_chars=len(result.final_response),
                final_response_preview=result.final_response[:200],
                assistant_message_count=(
                    len(assistant_messages)
                    if isinstance(assistant_messages, list)
                    else 0
                ),
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
            if isinstance(assistant_messages, list) and assistant_messages:
                last = assistant_messages[-1]
                parts: dict[str, Any] = {}
                if last.reasoning:
                    parts["reasoning"] = last.reasoning
                if last.reasoning_signature:
                    parts["reasoning_signature"] = last.reasoning_signature
                if last.has_redacted_thinking:
                    parts["has_redacted_thinking"] = True
                if parts:
                    assistant_metadata = parts
            assistant_context_metadata = message_context_to_metadata(
                assistant_context() if message_context is not None else None
            )
            if assistant_context_metadata is not None:
                if assistant_metadata is None:
                    assistant_metadata = {}
                assistant_metadata["message_context"] = assistant_context_metadata

            assistant_turn = ConversationTurn(
                role="assistant",
                content=result.final_response,
                source="agent_response",
                metadata=assistant_metadata,
            )
            await self._memory.append_turn(session_id, assistant_turn)

        await self._consolidate_memory_after_turn(
            session_id=session_id,
            user_message=user_message,
            assistant_message=str(getattr(result, "final_response", "") or ""),
            workspace_id=workspace_id,
            workspace_root=workspace_root,
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
            applied = await self._memory_consolidator.consolidate_turn(
                session_id=session_id,
                user_message=user_message,
                assistant_message=assistant_message,
                workspace_id=workspace_id,
                workspace_root=resolved_root,
            )
            if applied:
                logger.debug(
                    "session_runner.memory_consolidated",
                    session_id=session_id,
                    workspace_id=workspace_id or "",
                    applied=applied,
                )
        except Exception as exc:
            logger.warning(
                "session_runner.memory_consolidation_failed",
                session_id=session_id,
                error=str(exc),
            )


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
