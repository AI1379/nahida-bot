"""MessageRouter — bridges MessageReceived events to commands and AgentLoop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import structlog

from nahida_bot.agent.context import ContextMessage
from nahida_bot.agent.memory.models import ConversationTurn
from nahida_bot.agent.providers import ToolDefinition
from nahida_bot.core.channel_registry import ChannelRegistry
from nahida_bot.core.events import (
    EventBus,
    MessagePayload,
    MessageReceived,
    MessageSending,
    MessageSent,
)
from nahida_bot.plugins.base import InboundMessage, OutboundMessage
from nahida_bot.plugins.commands import CommandMatcher, CommandRegistry

if TYPE_CHECKING:
    from nahida_bot.agent.loop import AgentLoop
    from nahida_bot.agent.memory.store import MemoryStore
    from nahida_bot.agent.providers.manager import ProviderManager
    from nahida_bot.core.events import EventContext, Subscription
    from nahida_bot.plugins.registry import ToolRegistry
    from nahida_bot.workspace.manager import WorkspaceManager

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class RouterConfig:
    """Configuration for the MessageRouter."""

    system_prompt: str = "You are a helpful assistant."
    max_history_turns: int = 50
    agent_enabled: bool = True


class MessageRouter:
    """Bridges MessageReceived events to command handlers and the AgentLoop.

    Subscribes to ``MessageReceived`` at priority=0 (sync phase) so that
    command matching happens deterministically before any plugin async
    handlers run.
    """

    def __init__(
        self,
        event_bus: EventBus,
        command_registry: CommandRegistry,
        command_matcher: CommandMatcher,
        channel_registry: ChannelRegistry,
        agent_loop: AgentLoop | None = None,
        memory_store: MemoryStore | None = None,
        provider_manager: ProviderManager | None = None,
        tool_registry: ToolRegistry | None = None,
        workspace_manager: WorkspaceManager | None = None,
        config: RouterConfig | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._commands = command_registry
        self._matcher = command_matcher
        self._channels = channel_registry
        self._agent = agent_loop
        self._memory = memory_store
        self._provider_manager = provider_manager
        self._tool_registry = tool_registry
        self._workspace = workspace_manager
        self._config = config or RouterConfig()
        self._subscription: Subscription | None = None
        # Maps deterministic session key → active session id (for /new)
        self._active_sessions: dict[str, str] = {}

    @property
    def agent(self) -> AgentLoop | None:
        """The agent loop, if configured."""
        return self._agent

    @agent.setter
    def agent(self, value: AgentLoop | None) -> None:
        self._agent = value

    @property
    def memory(self) -> MemoryStore | None:
        """The memory store, if configured."""
        return self._memory

    @memory.setter
    def memory(self, value: MemoryStore | None) -> None:
        self._memory = value

    @property
    def provider_manager(self) -> ProviderManager | None:
        """The provider manager, if configured."""
        return self._provider_manager

    @provider_manager.setter
    def provider_manager(self, value: ProviderManager | None) -> None:
        self._provider_manager = value

    async def start(self) -> None:
        """Subscribe to MessageReceived events."""
        self._subscription = self._event_bus.subscribe(
            MessageReceived,
            self._handle_message_received,
            priority=0,
            timeout=120.0,
        )
        logger.info("message_router.started")

    async def stop(self) -> None:
        """Unsubscribe from events."""
        if self._subscription is not None:
            self._subscription.unsubscribe()
            self._subscription = None
        logger.info("message_router.stopped")

    def get_active_session_id(self, platform: str, chat_id: str) -> str:
        """Return the active session ID for a chat.

        If ``/new`` was used, returns the switched session id.
        Otherwise returns the deterministic ``platform:chat_id`` key.
        """
        key = self.make_session_id(platform, chat_id)
        active = self._active_sessions.get(key, key)
        logger.debug(
            "router.resolve_session",
            key=key,
            active_session_id=active,
            has_override=key in self._active_sessions,
        )
        return active

    def set_active_session(self, platform: str, chat_id: str, session_id: str) -> None:
        """Switch the active session for a chat (used by /new)."""
        key = self.make_session_id(platform, chat_id)
        old = self._active_sessions.get(key, key)
        self._active_sessions[key] = session_id
        logger.debug(
            "router.set_active_session",
            key=key,
            old_session_id=old,
            new_session_id=session_id,
        )

    async def _handle_message_received(
        self, event: MessageReceived, ctx: EventContext
    ) -> None:
        """Core dispatch logic: command first, then agent."""
        inbound: InboundMessage = event.payload.message
        session_id = self.get_active_session_id(inbound.platform, inbound.chat_id)
        logger.debug(
            "router.dispatch",
            platform=inbound.platform,
            chat_id=inbound.chat_id,
            session_id=session_id,
            text_preview=inbound.text[:100],
        )
        workspace_id, workspace_root = self._resolve_workspace_context()

        # Step 1: Command matching
        match = self._matcher.match(inbound.text, prefix=inbound.command_prefix)
        if match.matched:
            entry = self._commands.get(match.name)
            if entry is not None:
                response_text = await entry.handler(
                    args=match.args,
                    inbound=inbound,
                    session_id=session_id,
                )
                await self._send_response(inbound, session_id, response_text)
                return

        # Step 2: Agent loop (if configured)
        if self._agent is None or not self._config.agent_enabled:
            return

        # Resolve provider for this session
        provider_slot = await self._resolve_provider(session_id)

        # Load history from memory
        history = await self._load_history(session_id, workspace_id=workspace_id)

        # Build run kwargs — include provider override when available
        run_kwargs: dict[str, Any] = {
            "user_message": inbound.text,
            "system_prompt": self._config.system_prompt,
            "history_messages": history,
        }
        if workspace_root is not None:
            run_kwargs["workspace_root"] = workspace_root
        tools = self._registered_tools()
        if tools:
            run_kwargs["tools"] = tools
        if provider_slot is not None:
            run_kwargs["provider"] = provider_slot.provider
            run_kwargs["context_builder"] = provider_slot.context_builder

        result = await self._agent.run(**run_kwargs)

        # Persist turns
        await self._persist_turns(session_id, inbound, result)

        # Send response
        await self._send_response(inbound, session_id, result.final_response)

    async def _resolve_provider(self, session_id: str) -> Any:
        """Resolve the provider slot for a session.

        Checks session metadata for a model/provider preference,
        falls back to the default provider.
        """
        if self._provider_manager is None:
            return None

        # Check session metadata
        if self._memory is not None:
            meta = await self._memory.get_session_meta(session_id)
            if meta:
                model = meta.get("model")
                if model:
                    slot = self._provider_manager.resolve_model(model)
                    if slot is not None:
                        return slot
                provider_id = meta.get("provider_id")
                if provider_id:
                    slot = self._provider_manager.get(provider_id)
                    if slot is not None:
                        return slot

        return self._provider_manager.default

    async def _send_response(
        self, inbound: InboundMessage, session_id: str, text: str
    ) -> None:
        """Send response through the originating channel."""
        if not text:
            return

        channel = self._channels.get(inbound.platform)
        if channel is None:
            logger.warning(
                "message_router.no_channel",
                platform=inbound.platform,
            )
            return

        outbound = OutboundMessage(text=text, reply_to=inbound.message_id)

        # Publish MessageSending event (plugins can intercept/modify)
        await self._event_bus.publish(
            MessageSending(
                payload=MessagePayload(message=inbound, session_id=session_id),
                source="message_router",
            )
        )

        # Send via channel
        msg_id = await channel.send_message(inbound.chat_id, outbound)

        # Publish MessageSent event
        await self._event_bus.publish(
            MessageSent(
                payload=MessagePayload(message=inbound, session_id=session_id),
                source="message_router",
            )
        )

        logger.debug(
            "message_router.response_sent",
            platform=inbound.platform,
            chat_id=inbound.chat_id,
            msg_id=msg_id,
        )

    async def _load_history(
        self, session_id: str, *, workspace_id: str | None = None
    ) -> list[ContextMessage]:
        """Load conversation history from memory store."""
        if self._memory is None:
            logger.debug("router.load_history.no_memory")
            return []

        await self._memory.ensure_session(session_id, workspace_id=workspace_id)
        records = await self._memory.get_recent(
            session_id, limit=self._config.max_history_turns
        )
        logger.debug(
            "router.load_history",
            session_id=session_id,
            workspace_id=workspace_id,
            record_count=len(records),
            preview_roles=[r.turn.role for r in records[:6]],
        )
        return [
            ContextMessage(
                role=r.turn.role,  # type: ignore[arg-type]
                content=r.turn.content,
                source=r.turn.source,
            )
            for r in records
        ]

    def _resolve_workspace_context(self) -> tuple[str | None, Any | None]:
        """Return active workspace id and root path for context injection."""
        if self._workspace is None:
            return None, None

        metadata = self._workspace.get_active_workspace()
        root = self._workspace.workspace_path(metadata.workspace_id)
        return metadata.workspace_id, root

    def _registered_tools(self) -> list[ToolDefinition]:
        """Return provider-facing definitions for all registered plugin tools."""
        if self._tool_registry is None:
            return []
        return [
            ToolDefinition(
                name=entry.name,
                description=entry.description,
                parameters=entry.parameters,
            )
            for entry in self._tool_registry.all()
        ]

    async def _persist_turns(
        self, session_id: str, inbound: InboundMessage, result: Any
    ) -> None:
        """Persist user message and agent response to memory."""
        if self._memory is None:
            return

        logger.debug(
            "router.persist_turns",
            session_id=session_id,
            user_preview=inbound.text[:80],
        )

        user_turn = ConversationTurn(
            role="user",
            content=inbound.text,
            source="user_input",
        )
        await self._memory.append_turn(session_id, user_turn)

        assistant_turn = ConversationTurn(
            role="assistant",
            content=result.final_response,
            source="agent_response",
        )
        await self._memory.append_turn(session_id, assistant_turn)

    @staticmethod
    def make_session_id(platform: str, chat_id: str) -> str:
        """Deterministic session ID from platform + chat_id."""
        return f"{platform}:{chat_id}"

    @staticmethod
    def make_new_session_id(platform: str, chat_id: str) -> str:
        """Generate a new unique session ID for /new."""
        suffix = uuid4().hex[:8]
        return f"{platform}:{chat_id}:{suffix}"
