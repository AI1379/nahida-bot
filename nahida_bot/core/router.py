"""MessageRouter — bridges MessageReceived events to commands and AgentLoop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

from nahida_bot.agent.context import ContextMessage
from nahida_bot.agent.memory.models import ConversationTurn
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
    from nahida_bot.core.events import EventContext, Subscription

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
        config: RouterConfig | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._commands = command_registry
        self._matcher = command_matcher
        self._channels = channel_registry
        self._agent = agent_loop
        self._memory = memory_store
        self._config = config or RouterConfig()
        self._subscription: Subscription | None = None

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

    async def _handle_message_received(
        self, event: MessageReceived, ctx: EventContext
    ) -> None:
        """Core dispatch logic: command first, then agent."""
        inbound: InboundMessage = event.payload.message
        session_id = event.payload.session_id

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

        # Load history from memory
        history = await self._load_history(session_id)

        # Run agent
        result = await self._agent.run(
            user_message=inbound.text,
            system_prompt=self._config.system_prompt,
            history_messages=history,
        )

        # Persist turns
        await self._persist_turns(session_id, inbound, result)

        # Send response
        await self._send_response(inbound, session_id, result.final_response)

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

    async def _load_history(self, session_id: str) -> list[ContextMessage]:
        """Load conversation history from memory store."""
        if self._memory is None:
            return []

        await self._memory.ensure_session(session_id)
        records = await self._memory.get_recent(
            session_id, limit=self._config.max_history_turns
        )
        return [
            ContextMessage(
                role=r.turn.role,  # type: ignore[arg-type]
                content=r.turn.content,
                source=r.turn.source,
            )
            for r in records
        ]

    async def _persist_turns(
        self, session_id: str, inbound: InboundMessage, result: Any
    ) -> None:
        """Persist user message and agent response to memory."""
        if self._memory is None:
            return

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
