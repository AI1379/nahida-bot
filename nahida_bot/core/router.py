"""MessageRouter — bridges MessageReceived events to commands and AgentLoop."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

import structlog

from nahida_bot.core.channel_registry import ChannelRegistry
from nahida_bot.core.context import SessionContext, current_session
from nahida_bot.core.events import (
    EventBus,
    MessagePayload,
    MessageReceived,
    MessageSending,
    MessageSent,
)
from nahida_bot.core.message_context import context_from_inbound
from nahida_bot.plugins.base import InboundMessage, OutboundMessage
from nahida_bot.plugins.commands import (
    CommandEntry,
    CommandHandlerResult,
    CommandMatcher,
    CommandRegistry,
    CommandResult,
)

if TYPE_CHECKING:
    from nahida_bot.agent.loop import AgentLoop
    from nahida_bot.agent.memory.store import MemoryStore
    from nahida_bot.agent.providers.manager import ProviderManager
    from nahida_bot.core.events import EventContext, Subscription
    from nahida_bot.core.session_runner import SessionRunner
    from nahida_bot.workspace.manager import WorkspaceManager

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class RouterConfig:
    """Configuration for the MessageRouter."""

    system_prompt: str = "You are a helpful assistant."
    max_history_turns: int = 50
    agent_enabled: bool = True
    command_timeout_seconds: float = 30.0
    command_timeout_message: str = "Command timed out. Please try again later."
    show_reasoning: bool = False
    reasoning_max_chars: int = 2000


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
        runner: SessionRunner | None = None,
        workspace_manager: WorkspaceManager | None = None,
        config: RouterConfig | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._commands = command_registry
        self._matcher = command_matcher
        self._channels = channel_registry
        self._runner = runner
        self._workspace = workspace_manager
        self._config = config or RouterConfig()
        self._subscription: Subscription | None = None
        # Maps deterministic session key → active session id (for /new)
        self._active_sessions: dict[str, str] = {}

    @property
    def agent(self) -> AgentLoop | None:
        """The agent loop, if configured."""
        return self._runner.agent if self._runner is not None else None

    @agent.setter
    def agent(self, value: AgentLoop | None) -> None:
        if self._runner is not None:
            self._runner.agent = value

    @property
    def memory(self) -> MemoryStore | None:
        """The memory store, if configured."""
        return self._runner.memory if self._runner is not None else None

    @memory.setter
    def memory(self, value: MemoryStore | None) -> None:
        if self._runner is not None:
            self._runner.memory = value

    @property
    def provider_manager(self) -> ProviderManager | None:
        """The provider manager, if configured."""
        return self._runner.provider_manager if self._runner is not None else None

    @provider_manager.setter
    def provider_manager(self, value: ProviderManager | None) -> None:
        if self._runner is not None:
            self._runner.provider_manager = value

    async def start(self) -> None:
        """Subscribe to MessageReceived events and restore session overrides."""
        self._subscription = self._event_bus.subscribe(
            MessageReceived,
            self._handle_message_received,
            priority=0,
            timeout=120.0,
        )
        await self.restore_active_sessions()
        logger.info("message_router.started")

    async def stop(self) -> None:
        """Unsubscribe from events."""
        if self._subscription is not None:
            self._subscription.unsubscribe()
            self._subscription = None
        logger.info("message_router.stopped")

    def _persist_override(self, key: str, session_id: str) -> None:
        """Fire-and-forget persist of the session override."""
        memory = self.memory
        if memory is None:
            return

        async def _do_persist() -> None:
            try:
                await memory.persist_active_session(key, session_id)
            except Exception:
                logger.warning("router.persist_override_failed", key=key, exc_info=True)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_do_persist())
        except RuntimeError:
            pass

    async def restore_active_sessions(self) -> None:
        """Load persisted session overrides from the memory store."""
        memory = self.memory
        if memory is None:
            return
        try:
            overrides = await memory.load_active_sessions()
            if overrides:
                self._active_sessions.update(overrides)
                logger.info(
                    "router.restored_sessions",
                    count=len(overrides),
                    keys=list(overrides.keys()),
                )
        except Exception:
            logger.warning("router.restore_sessions_failed", exc_info=True)

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
        self._persist_override(key, session_id)
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
        workspace_id = self._resolve_workspace_id()

        # Set session context so tool handlers can access it
        session_ctx = SessionContext(
            platform=inbound.platform,
            chat_id=inbound.chat_id,
            session_id=session_id,
            workspace_id=workspace_id,
        )
        token = current_session.set(session_ctx)
        try:
            await self._dispatch_message(inbound, session_id, workspace_id)
        finally:
            current_session.reset(token)

    async def _dispatch_message(
        self,
        inbound: InboundMessage,
        session_id: str,
        workspace_id: str | None,
    ) -> None:
        """Command matching + agent execution (called within session context)."""
        # Step 1: Command matching
        match = self._matcher.match(inbound.text, prefix=inbound.command_prefix)
        if match.matched:
            entry = self._commands.get(match.name)
            if entry is not None:
                logger.debug(
                    "router.command_matched",
                    command=match.name,
                    session_id=session_id,
                    platform=inbound.platform,
                    chat_id=inbound.chat_id,
                    args_preview=match.args[:80],
                )
                result = await self._execute_command(
                    entry=entry,
                    args=match.args,
                    inbound=inbound,
                    session_id=session_id,
                )
                outbound = self._coerce_command_result(
                    result,
                    default_reply_to=inbound.message_id,
                )
                if outbound is not None:
                    active_after_command = self.get_active_session_id(
                        inbound.platform, inbound.chat_id
                    )
                    logger.debug(
                        "router.command_completed",
                        command=match.name,
                        original_session_id=session_id,
                        active_session_id=active_after_command,
                        active_session_changed=active_after_command != session_id,
                    )
                    await self._send_outbound(inbound, session_id, outbound)
                return

        # Step 2: Agent loop (if configured)
        if self._runner is None or not self._runner.has_agent:
            return
        if not self._config.agent_enabled:
            return

        last_sent = ""
        async for event in self._runner.run_stream(
            user_message=inbound.text,
            session_id=session_id,
            system_prompt=self._config.system_prompt,
            workspace_id=workspace_id,
            attachments=inbound.attachments,
            message_context=context_from_inbound(inbound),
            source_tag="user_input",
        ):
            if event.type == "text":
                reasoning = self._prepare_reasoning(event.reasoning)
                if event.text and event.text != last_sent:
                    await self._send_response(
                        inbound, session_id, event.text, reasoning=reasoning
                    )
                    last_sent = event.text
                elif reasoning and not event.text:
                    await self._send_response(
                        inbound, session_id, "", reasoning=reasoning
                    )
            elif event.type == "done":
                final = event.final_response or ""
                reasoning = self._prepare_reasoning(event.reasoning)
                if final and final != last_sent:
                    await self._send_response(
                        inbound, session_id, final, reasoning=reasoning
                    )

    def _prepare_reasoning(self, reasoning: str | None) -> str:
        """Truncate reasoning if display is enabled."""
        if not self._config.show_reasoning or not reasoning:
            return ""
        limit = self._config.reasoning_max_chars
        if limit and len(reasoning) > limit:
            return reasoning[:limit] + "..."
        return reasoning

    async def _send_response(
        self,
        inbound: InboundMessage,
        session_id: str,
        text: str,
        *,
        reasoning: str = "",
    ) -> None:
        """Send response through the originating channel."""
        if not text and not reasoning:
            return

        await self._send_outbound(
            inbound,
            session_id,
            OutboundMessage(
                text=text, reply_to=inbound.message_id, reasoning=reasoning
            ),
        )

    async def _send_outbound(
        self, inbound: InboundMessage, session_id: str, outbound: OutboundMessage
    ) -> None:
        """Send an outbound message through the originating channel."""
        if not outbound.text and not outbound.attachments:
            return

        channel = self._channels.get(inbound.platform)
        if channel is None:
            logger.warning(
                "message_router.no_channel",
                platform=inbound.platform,
            )
            return

        # Publish MessageSending event for observation/audit hooks.
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

    async def _execute_command(
        self,
        *,
        entry: CommandEntry,
        args: str,
        inbound: InboundMessage,
        session_id: str,
    ) -> CommandHandlerResult:
        """Run a command handler with router-level timeout protection."""
        try:
            return await asyncio.wait_for(
                entry.handler(args=args, inbound=inbound, session_id=session_id),
                timeout=self._config.command_timeout_seconds,
            )
        except TimeoutError:
            logger.warning(
                "message_router.command_timeout",
                command=entry.name,
                plugin_id=entry.plugin_id,
                timeout=self._config.command_timeout_seconds,
            )
            return self._config.command_timeout_message

    def _coerce_command_result(
        self, result: CommandHandlerResult, *, default_reply_to: str = ""
    ) -> OutboundMessage | None:
        """Normalize supported command return values to OutboundMessage."""
        if result is None:
            return None
        if isinstance(result, str):
            if not result:
                return None
            return OutboundMessage(text=result, reply_to=default_reply_to)
        if isinstance(result, OutboundMessage):
            return result
        if isinstance(result, CommandResult):
            if result.suppress_response:
                return None
            return result.message

        logger.warning(
            "message_router.command_result_unsupported",
            result_type=type(result).__name__,
        )
        return OutboundMessage(text=str(result))

    def _resolve_workspace_id(self) -> str | None:
        """Return active workspace id for context injection."""
        if self._workspace is None:
            return None
        metadata = self._workspace.get_active_workspace()
        return metadata.workspace_id

    @staticmethod
    def make_session_id(platform: str, chat_id: str) -> str:
        """Deterministic session ID from platform + chat_id."""
        return f"{platform}:{chat_id}"

    @staticmethod
    def make_new_session_id(platform: str, chat_id: str) -> str:
        """Generate a new unique session ID for /new."""
        suffix = uuid4().hex[:8]
        return f"{platform}:{chat_id}:{suffix}"
