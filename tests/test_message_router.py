"""Tests for MessageRouter."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

from nahida_bot.agent.loop import LoopEvent
from nahida_bot.agent.memory.models import (
    ConversationTurn,
    MemoryRecord,
    SessionSummary,
)
from nahida_bot.agent.memory.store import MemoryStore
from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.channel_registry import ChannelRegistry
from nahida_bot.core.events import (
    AgentResponseRequested,
    AgentResponseRequestPayload,
    AgentRunCancelled,
    AgentRunFinished,
    AgentRunStarted,
    AgentStopPayload,
    AgentStopRequested,
    EventBus,
    EventContext,
    MessageObserved,
    MessageReceived,
    MessagePayload,
    MessageSent,
)
from nahida_bot.core.router import MessageRouter, RouterConfig
from nahida_bot.core.session_runner import SessionRunner
from nahida_bot.core.temp_files import ManagedTempFileService
from nahida_bot.plugins.base import (
    AttentionFrame,
    InboundMessage,
    MessageContext,
    OutboundMessage,
    Plugin,
)
from nahida_bot.plugins.commands import (
    CommandEntry,
    CommandMatcher,
    CommandRegistry,
    CommandResult,
)
from nahida_bot.plugins.manifest import PluginManifest
from nahida_bot.plugins.registry import ToolEntry, ToolRegistry
from nahida_bot.workspace.manager import WorkspaceManager


# ── Helpers ──────────────────────────────────────────────────


def _inbound(text: str = "hello", platform: str = "test") -> InboundMessage:
    from nahida_bot.plugins.base import ChatContext

    return InboundMessage(
        message_id="1",
        platform=platform,
        chat_id="c1",
        user_id="u1",
        text=text,
        raw_event={},
        chat_context=ChatContext(platform=platform, chat_type="private"),
    )


class _StubChannel(Plugin):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._channel_id = self.manifest.id
        self.reply_to_inbound: bool | None = None
        self.sent: list[tuple[str, OutboundMessage]] = []

    @property
    def channel_id(self) -> str:
        return self._channel_id

    async def on_load(self) -> None:
        pass

    async def handle_inbound_event(self, event: dict[str, Any]) -> None:
        pass

    async def send_message(self, target: str, message: OutboundMessage) -> str:
        self.sent.append((target, message))
        return "msg_1"


class _MockMemoryStore(MemoryStore):
    """Minimal MemoryStore mock."""

    def __init__(self) -> None:
        self.sessions: dict[str, list[ConversationTurn]] = {}
        self.workspace_ids: dict[str, str | None] = {}
        self.persisted_overrides: dict[str, str] = {}
        self.session_meta: dict[str, dict[str, Any]] = {}
        self.get_recent_calls = 0

    async def ensure_session(
        self, session_id: str, workspace_id: str | None = None
    ) -> None:
        self.sessions.setdefault(session_id, [])
        self.workspace_ids[session_id] = workspace_id

    async def append_turn(self, session_id: str, turn: ConversationTurn) -> int:
        self.sessions.setdefault(session_id, []).append(turn)
        return len(self.sessions[session_id])

    async def get_recent(
        self, session_id: str, *, limit: int = 50
    ) -> list[MemoryRecord]:
        self.get_recent_calls += 1
        turns = self.sessions.get(session_id, [])
        return [
            MemoryRecord(turn_id=i, session_id=session_id, turn=t)
            for i, t in enumerate(turns[-limit:])
        ]

    async def search(
        self, session_id: str, query: str, *, limit: int = 10
    ) -> list[MemoryRecord]:
        return []

    async def evict_before(self, cutoff: datetime) -> int:
        return 0

    async def clear_session(self, session_id: str) -> int:
        turns = self.sessions.pop(session_id, [])
        self.workspace_ids.pop(session_id, None)
        self.session_meta.pop(session_id, None)
        return len(turns)

    async def list_sessions(self, *, limit: int = 50) -> list[SessionSummary]:
        now = datetime.now(UTC).isoformat()
        summaries = [
            SessionSummary(
                session_id=session_id,
                workspace_id=self.workspace_ids.get(session_id),
                created_at=now,
                last_active_at=now,
                turn_count=len(turns),
                metadata=dict(self.session_meta.get(session_id, {})),
            )
            for session_id, turns in self.sessions.items()
        ]
        return summaries[:limit]

    async def persist_active_session(self, chat_key: str, session_id: str) -> None:
        self.persisted_overrides[chat_key] = session_id

    async def load_active_sessions(self) -> dict[str, str]:
        return dict(self.persisted_overrides)

    async def get_session_meta(self, session_id: str) -> dict[str, Any]:
        return dict(self.session_meta.get(session_id, {}))

    async def update_session_meta(
        self, session_id: str, updates: dict[str, Any]
    ) -> None:
        self.session_meta.setdefault(session_id, {}).update(updates)


class _MockAgentLoop:
    """Minimal AgentLoop mock."""

    def __init__(self, response: str = "agent reply") -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def run(self, **kwargs: Any) -> Any:
        return await _collect_run_result(self.run_stream(**kwargs))

    async def run_stream(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        yield LoopEvent(type="text", text=self.response)
        yield LoopEvent(type="done", final_response=self.response)


async def _collect_run_result(stream: Any) -> Any:
    """Consume an async generator of LoopEvents and return a mock AgentRunResult."""
    async for event in stream:
        if event.type == "done":
            result = MagicMock()
            result.final_response = event.final_response or ""
            result.assistant_messages = event.assistant_messages or []
            result.tool_messages = event.tool_messages or []
            result.steps = event.steps
            result.trace_id = event.trace_id
            result.error = event.error
            return result
    result = MagicMock()
    result.final_response = ""
    return result


def _make_router(
    *,
    agent: Any = None,
    memory: Any = None,
    tool_registry: ToolRegistry | None = None,
    workspace_manager: WorkspaceManager | None = None,
    config: RouterConfig | None = None,
    temp_file_service: ManagedTempFileService | None = None,
) -> tuple[MessageRouter, EventBus, ChannelRegistry, CommandRegistry]:
    event_bus = EventBus(EventContext(app=None, settings=None, logger=MagicMock()))  # type: ignore[arg-type]
    command_registry = CommandRegistry()
    command_matcher = CommandMatcher()
    channel_registry = ChannelRegistry()

    # Register a stub channel for the "test" platform
    manifest = PluginManifest(id="test", name="Test", version="1.0", entrypoint="t:T")
    channel = _StubChannel(api=MagicMock(), manifest=manifest)
    channel_registry.register(channel)

    runner = SessionRunner(
        agent_loop=agent,
        memory_store=memory,
        tool_registry=tool_registry,
        workspace_manager=workspace_manager,
    )

    router = MessageRouter(
        event_bus=event_bus,
        command_registry=command_registry,
        command_matcher=command_matcher,
        channel_registry=channel_registry,
        runner=runner,
        workspace_manager=workspace_manager,
        config=config,
        temp_file_service=temp_file_service,
    )
    return router, event_bus, channel_registry, command_registry


# ── Tests ────────────────────────────────────────────────────


class TestMessageRouterSessionId:
    def test_session_id_format(self) -> None:
        address = ChatAddress(
            channel="telegram", target_type="private", target_id="123"
        )
        assert MessageRouter.make_session_id(address) == "telegram:private:123"

    def test_session_id_deterministic(self) -> None:
        address = ChatAddress(channel="qq", target_type="group", target_id="456")
        a = MessageRouter.make_session_id(address)
        b = MessageRouter.make_session_id(address)
        assert a == b

    def test_legacy_active_session_id_falls_back_to_legacy_override(self) -> None:
        router, _, _, _ = _make_router()
        router._active_sessions["telegram:123"] = "telegram:123:override"

        assert (
            router.get_active_session_id(
                ChatAddress(channel="telegram", target_type="unknown", target_id="123")
            )
            == "telegram:123:override"
        )

    def test_typed_active_session_id_uses_typed_override(self) -> None:
        router, _, _, _ = _make_router()
        address = ChatAddress(
            channel="telegram", target_type="private", target_id="123"
        )
        router._active_sessions[str(address)] = f"{address}:abc12345"

        assert router.get_active_session_id(address) == "telegram:private:123:abc12345"
        assert (
            router.get_active_session_id(
                ChatAddress(channel="telegram", target_type="unknown", target_id="123")
            )
            == "telegram:123"
        )


class TestMessageRouterCommandDispatch:
    async def test_command_match_dispatches_to_handler(self) -> None:
        router, event_bus, channel_registry, command_registry = _make_router()

        handler_response = "pong!"
        handler = AsyncMock(return_value=handler_response)
        command_registry.register(
            CommandEntry(
                name="ping",
                handler=handler,
                description="Ping",
                aliases=(),
                plugin_id="p1",
            )
        )

        await router.start()
        inbound = _inbound("/ping")
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(
                    message=inbound,
                    session_id="test:private:c1",
                ),
                source="test",
            )
        )
        await router.stop()

        handler.assert_awaited_once()
        assert handler.call_args.kwargs["args"] == ""
        channel = channel_registry.get("test")
        assert isinstance(channel, _StubChannel)
        assert channel.sent[0][1].reply_to == "1"

    async def test_command_with_args(self) -> None:
        router, event_bus, _, command_registry = _make_router()

        handler = AsyncMock(return_value="ok")
        command_registry.register(
            CommandEntry(
                name="echo",
                handler=handler,
                description="Echo",
                aliases=(),
                plugin_id="p1",
            )
        )

        await router.start()
        inbound = _inbound("/echo hello world")
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(message=inbound, session_id="test:private:c1"),
                source="test",
            )
        )
        await router.stop()

        handler.assert_awaited_once()
        assert handler.call_args.kwargs["args"] == "hello world"

    async def test_command_reply_to_can_be_disabled_globally(self) -> None:
        router, event_bus, channel_registry, command_registry = _make_router(
            config=RouterConfig(reply_to_inbound=False)
        )
        handler = AsyncMock(return_value="ok")
        command_registry.register(
            CommandEntry(
                name="ping",
                handler=handler,
                description="Ping",
                aliases=(),
                plugin_id="p1",
            )
        )

        await router.start()
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(message=_inbound("/ping"), session_id=""),
                source="test",
            )
        )
        await router.stop()

        channel = channel_registry.get("test")
        assert isinstance(channel, _StubChannel)
        assert channel.sent[0][1].reply_to == ""

    async def test_channel_reply_to_override_can_disable_global_default(self) -> None:
        router, event_bus, channel_registry, command_registry = _make_router(
            config=RouterConfig(reply_to_inbound=True)
        )
        channel = channel_registry.get("test")
        assert isinstance(channel, _StubChannel)
        channel.reply_to_inbound = False
        handler = AsyncMock(return_value="ok")
        command_registry.register(
            CommandEntry(
                name="ping",
                handler=handler,
                description="Ping",
                aliases=(),
                plugin_id="p1",
            )
        )

        await router.start()
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(message=_inbound("/ping"), session_id=""),
                source="test",
            )
        )
        await router.stop()

        assert channel.sent[0][1].reply_to == ""

    async def test_channel_reply_to_override_can_enable_global_disabled(self) -> None:
        router, event_bus, channel_registry, command_registry = _make_router(
            config=RouterConfig(reply_to_inbound=False)
        )
        channel = channel_registry.get("test")
        assert isinstance(channel, _StubChannel)
        channel.reply_to_inbound = True
        handler = AsyncMock(return_value="ok")
        command_registry.register(
            CommandEntry(
                name="ping",
                handler=handler,
                description="Ping",
                aliases=(),
                plugin_id="p1",
            )
        )

        await router.start()
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(message=_inbound("/ping"), session_id=""),
                source="test",
            )
        )
        await router.stop()

        assert channel.sent[0][1].reply_to == "1"

    async def test_command_can_return_outbound_message(self) -> None:
        router, event_bus, channel_registry, command_registry = _make_router()
        outbound = OutboundMessage(text="file attached", attachments=[])
        handler = AsyncMock(return_value=outbound)
        command_registry.register(
            CommandEntry(
                name="report",
                handler=handler,
                description="Report",
                aliases=(),
                plugin_id="p1",
            )
        )

        await router.start()
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(message=_inbound("/report"), session_id=""),
                source="test",
            )
        )
        await router.stop()

        channel = channel_registry.get("test")
        assert isinstance(channel, _StubChannel)
        assert channel.sent[0][1] is outbound

    async def test_command_returned_managed_temp_attachment_is_cleaned(
        self, tmp_path: Path
    ) -> None:
        temp_file_service = ManagedTempFileService(tmp_path / "plugin_temp")
        temp_file = await temp_file_service.create_temp_file(
            plugin_id="p1",
            suffix=".png",
            purpose="test-command",
        )
        temp_path = Path(temp_file.path)
        temp_path.write_bytes(b"png")
        meta_path = temp_path.with_name(f"{temp_path.name}.meta.json")
        router, event_bus, channel_registry, command_registry = _make_router(
            temp_file_service=temp_file_service
        )
        handler = AsyncMock(
            return_value=CommandResult.outbound(
                OutboundMessage(
                    text="file attached",
                    attachments=[
                        temp_file.as_attachment(type="photo", mime_type="image/png")
                    ],
                )
            )
        )
        command_registry.register(
            CommandEntry(
                name="report",
                handler=handler,
                description="Report",
                aliases=(),
                plugin_id="p1",
            )
        )

        await router.start()
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(message=_inbound("/report"), session_id=""),
                source="test",
            )
        )
        await router.stop()

        channel = channel_registry.get("test")
        assert isinstance(channel, _StubChannel)
        assert len(channel.sent[0][1].attachments) == 1
        assert not temp_path.exists()
        assert not meta_path.exists()

    async def test_onebot_outbound_includes_typed_chat_address(self) -> None:
        from nahida_bot.plugins.base import ChatContext

        router, event_bus, channel_registry, command_registry = _make_router()
        manifest = PluginManifest(
            id="onebot",
            name="OneBot",
            version="1.0",
            entrypoint="t:T",
        )
        onebot_channel = _StubChannel(api=MagicMock(), manifest=manifest)
        channel_registry.register(onebot_channel)
        handler = AsyncMock(return_value="ok")
        command_registry.register(
            CommandEntry(
                name="ping",
                handler=handler,
                description="Ping",
                aliases=(),
                plugin_id="p1",
            )
        )
        inbound = InboundMessage(
            message_id="1",
            platform="onebot",
            chat_id="20001",
            user_id="10001",
            text="/ping",
            raw_event={},
            is_group=True,
            chat_context=ChatContext(platform="onebot", chat_type="group"),
        )

        await router.start()
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(message=inbound, session_id=""),
                source="onebot",
            )
        )
        await router.stop()

        assert onebot_channel.sent[0][0] == "20001"
        assert onebot_channel.sent[0][1].extra["chat_address"] == ("onebot:group:20001")

    async def test_command_can_suppress_response(self) -> None:
        router, event_bus, channel_registry, command_registry = _make_router()
        handler = AsyncMock(return_value=CommandResult.none())
        command_registry.register(
            CommandEntry(
                name="silent",
                handler=handler,
                description="Silent",
                aliases=(),
                plugin_id="p1",
            )
        )

        await router.start()
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(message=_inbound("/silent"), session_id=""),
                source="test",
            )
        )
        await router.stop()

        channel = channel_registry.get("test")
        assert isinstance(channel, _StubChannel)
        assert channel.sent == []

    async def test_command_timeout_returns_timeout_message(self) -> None:
        router, event_bus, channel_registry, command_registry = _make_router(
            config=RouterConfig(
                command_timeout_seconds=0.01,
                command_timeout_message="too slow",
            )
        )

        async def _slow(**kwargs: object) -> str:
            await asyncio.sleep(1)
            return "done"

        command_registry.register(
            CommandEntry(
                name="slow",
                handler=_slow,
                description="Slow",
                aliases=(),
                plugin_id="p1",
            )
        )

        await router.start()
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(message=_inbound("/slow"), session_id=""),
                source="test",
            )
        )
        await router.stop()

        channel = channel_registry.get("test")
        assert isinstance(channel, _StubChannel)
        assert channel.sent[0][1].text == "too slow"


class TestMessageRouterAgentDispatch:
    async def test_no_command_dispatches_to_agent(self) -> None:
        agent = _MockAgentLoop(response="agent says hi")
        router, event_bus, _, _ = _make_router(agent=agent)

        await router.start()
        inbound = _inbound("what is 2+2?")
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(message=inbound, session_id="test:private:c1"),
                source="test",
            )
        )
        await router.stop()

        assert len(agent.calls) == 1
        visible_user_message = agent.calls[0]["user_message"]
        assert visible_user_message.startswith(
            '<message_context trust="untrusted" role="user">\n'
        )
        assert "channel: test/private:c1" in visible_user_message
        assert "sender: u1" in visible_user_message
        assert "text:\n  what is 2+2?" in visible_user_message

    async def test_node_input_uses_requested_session_and_source_tag(self) -> None:
        agent = _MockAgentLoop(response="node reply")
        memory = _MockMemoryStore()
        router, event_bus, channels, _ = _make_router(agent=agent, memory=memory)
        router.set_active_session(
            ChatAddress(channel="test", target_type="private", target_id="c1"),
            "test:private:c1:active",
        )
        sent_events: list[MessageSent] = []

        async def capture_sent(event: MessageSent, ctx: EventContext) -> None:
            sent_events.append(event)

        event_bus.subscribe(MessageSent, capture_sent)
        await router.start()
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(
                    message=_inbound("from desktop"),
                    session_id="test:private:c1:desktop",
                ),
                source="node:desktop-1",
            )
        )
        await router.stop()

        assert agent.calls[0]["session_id"] == "test:private:c1:desktop"
        assert memory.sessions["test:private:c1:desktop"][0].source == "node"
        assert sent_events[0].payload.outbound is not None
        assert sent_events[0].payload.outbound.text == "node reply"
        channel = channels.get("test")
        assert isinstance(channel, _StubChannel)
        assert channel.sent[0][1].reply_to == ""

    async def test_agent_response_requested_dispatches_proactive_join(self) -> None:
        from nahida_bot.plugins.base import ChatContext

        memory = _MockMemoryStore()
        agent = _MockAgentLoop(response="joining")
        router, event_bus, _, _ = _make_router(agent=agent, memory=memory)
        inbound = InboundMessage(
            message_id="m1",
            platform="test",
            chat_id="g1",
            user_id="u1",
            text="we are discussing deployments",
            raw_event={},
            is_group=True,
            chat_context=ChatContext(platform="test", chat_type="group"),
        )

        await router.start()
        await event_bus.publish(
            AgentResponseRequested(
                payload=AgentResponseRequestPayload(
                    message=inbound,
                    session_id="test:group:g1",
                    chat_address=ChatAddress(
                        channel="test",
                        target_type="group",
                        target_id="g1",
                    ),
                    requester_plugin_id="conversation_joiner",
                    reason="useful context",
                    instruction="focus on deployment status",
                ),
                source="conversation_joiner",
            )
        )
        await router.stop()

        assert len(agent.calls) == 1
        assert "Proactive Conversation Join" in agent.calls[0]["system_prompt"]
        assert "focus on deployment status" in agent.calls[0]["system_prompt"]
        turns = memory.sessions["test:group:g1"]
        assert turns[0].source == "proactive_join"

    async def test_agent_response_requested_batch_context_and_reply_anchor(
        self,
    ) -> None:
        from nahida_bot.plugins.base import ChatContext, SenderContext

        agent = _MockAgentLoop(response="agent reply")
        memory = _MockMemoryStore()
        router, event_bus, channel_registry, _ = _make_router(
            agent=agent,
            memory=memory,
            config=RouterConfig(reply_to_inbound=True),
        )
        inbound = InboundMessage(
            message_id="m2",
            platform="test",
            chat_id="g1",
            user_id="u2",
            text="but will it spam?",
            raw_event={},
            is_group=True,
            chat_context=ChatContext(platform="test", chat_type="group"),
            sender_context=SenderContext(display_name="Bob", platform_user_id="u2"),
        )
        batch = (
            InboundMessage(
                message_id="m1",
                platform="test",
                chat_id="g1",
                user_id="u1",
                text="should we enable this?",
                raw_event={},
                is_group=True,
                chat_context=ChatContext(platform="test", chat_type="group"),
                sender_context=SenderContext(
                    display_name="Alice",
                    platform_user_id="u1",
                ),
            ),
            inbound,
            InboundMessage(
                message_id="m3",
                platform="test",
                chat_id="g1",
                user_id="u3",
                text="cooldown may handle it",
                raw_event={},
                is_group=True,
                chat_context=ChatContext(platform="test", chat_type="group"),
                sender_context=SenderContext(
                    display_name="Carol",
                    platform_user_id="u3",
                ),
            ),
        )

        await router.start()
        # Real channel flow persists every untriggered batch message as
        # observed-only before ConversationJoiner asks for a proactive run.
        for observed_message in batch:
            await event_bus.publish(
                MessageObserved(
                    payload=MessagePayload(
                        message=observed_message,
                        session_id="test:group:g1",
                    ),
                    source="test",
                )
            )
        await event_bus.publish(
            AgentResponseRequested(
                payload=AgentResponseRequestPayload(
                    message=inbound,
                    session_id="test:group:g1",
                    chat_address=ChatAddress(
                        channel="test",
                        target_type="group",
                        target_id="g1",
                    ),
                    requester_plugin_id="conversation_joiner",
                    reason="batch",
                    instruction="focus on the spam concern",
                    observed_messages=batch,
                    reply_to_message_id="m2",
                    attention_frame=AttentionFrame(
                        trigger_kind="engaged_continue",
                        anchor_message_id="m2",
                        messages=batch,
                        reason="batch",
                        focus="spam concern",
                        reply_to_message_id="m2",
                        max_chars=2000,
                    ),
                ),
                source="conversation_joiner",
            )
        )
        await router.stop()

        system_prompt = agent.calls[0]["system_prompt"]
        user_message = agent.calls[0]["user_message"]
        history = agent.calls[0]["history_messages"]
        assert "Conversation Joiner Batch Context" not in system_prompt
        assert "Conversation Joiner Batch Context" not in user_message
        assert "but will it spam?" in user_message
        frames = [
            message
            for message in history
            if message.source == "proactive_attention_frame"
        ]
        assert len(frames) == 1
        assert not [
            message for message in history if message.source == "group_observed_context"
        ]
        assert "Conversation Joiner Batch Context" in frames[0].content
        assert "Batch message_id: m1" in frames[0].content
        assert "Batch message_id: m2" not in frames[0].content
        assert "Batch message_id: m3" in frames[0].content
        assert "Current anchor message_id: m2" in frames[0].content
        assert "Attention trigger: engaged_continue" in frames[0].content
        assert "Attention focus: spam concern" in frames[0].content
        assert (
            '<message_context trust="untrusted" role="batch_message">'
            in frames[0].content
        )
        assert "Reply anchor message_id: m2" in frames[0].content

        turns = memory.sessions["test:group:g1"]
        proactive_turns = [turn for turn in turns if turn.source == "proactive_join"]
        assert len(proactive_turns) == 1
        assert proactive_turns[0].content == "but will it spam?"
        assert "Conversation Joiner Batch Context" not in proactive_turns[0].content

        channel = channel_registry.get("test")
        assert isinstance(channel, _StubChannel)
        assert channel.sent[0][1].reply_to == "m2"

    async def test_agent_response_requested_batch_can_disable_reply_anchor(
        self,
    ) -> None:
        from nahida_bot.plugins.base import ChatContext

        agent = _MockAgentLoop(response="agent reply")
        router, event_bus, channel_registry, _ = _make_router(
            agent=agent,
            config=RouterConfig(reply_to_inbound=True),
        )
        inbound = InboundMessage(
            message_id="m2",
            platform="test",
            chat_id="g1",
            user_id="u2",
            text="ambient topic",
            raw_event={},
            is_group=True,
            chat_context=ChatContext(platform="test", chat_type="group"),
        )

        await router.start()
        await event_bus.publish(
            AgentResponseRequested(
                payload=AgentResponseRequestPayload(
                    message=inbound,
                    session_id="test:group:g1",
                    chat_address=ChatAddress(
                        channel="test",
                        target_type="group",
                        target_id="g1",
                    ),
                    requester_plugin_id="conversation_joiner",
                    reason="batch",
                    observed_messages=(inbound,),
                    reply_to_message_id="",
                ),
                source="conversation_joiner",
            )
        )
        await router.stop()

        frames = [
            message
            for message in agent.calls[0]["history_messages"]
            if message.source == "proactive_attention_frame"
        ]
        assert len(frames) == 1
        assert "Current anchor message_id: m2" in frames[0].content
        assert "ambient topic" not in frames[0].content

        channel = channel_registry.get("test")
        assert isinstance(channel, _StubChannel)
        assert channel.sent[0][1].reply_to == ""

    async def test_agent_response_requested_active_run_reports_failure(self) -> None:
        from nahida_bot.plugins.base import ChatContext

        agent = _MockAgentLoop(response="agent reply")
        router, event_bus, _, _ = _make_router(agent=agent)
        inbound = InboundMessage(
            message_id="m1",
            platform="test",
            chat_id="g1",
            user_id="u1",
            text="topic",
            raw_event={},
            is_group=True,
            chat_context=ChatContext(platform="test", chat_type="group"),
        )

        await router.start()
        task = asyncio.create_task(asyncio.sleep(60))
        runner = cast(Any, router)._runner
        runner.run_tracker.start("test:group:g1", task, asyncio.Event())
        try:
            result = await event_bus.publish(
                AgentResponseRequested(
                    payload=AgentResponseRequestPayload(
                        message=inbound,
                        session_id="test:group:g1",
                        chat_address=ChatAddress(
                            channel="test",
                            target_type="group",
                            target_id="g1",
                        ),
                        requester_plugin_id="conversation_joiner",
                        reason="batch",
                    ),
                    source="conversation_joiner",
                )
            )
        finally:
            runner.run_tracker.finish("test:group:g1")
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await router.stop()

        assert result.failures
        assert "active_run:test:group:g1" in result.failures[0].error
        assert agent.calls == []

    async def test_agent_response_requested_rejects_private_target(self) -> None:
        agent = _MockAgentLoop(response="should not run")
        router, event_bus, _, _ = _make_router(agent=agent)
        inbound = _inbound("hello")

        await router.start()
        await event_bus.publish(
            AgentResponseRequested(
                payload=AgentResponseRequestPayload(
                    message=inbound,
                    session_id="test:private:c1",
                    chat_address=ChatAddress(
                        channel="test",
                        target_type="private",
                        target_id="c1",
                    ),
                    requester_plugin_id="conversation_joiner",
                    reason="not allowed",
                ),
                source="conversation_joiner",
            )
        )
        await router.stop()

        assert agent.calls == []

    async def test_no_agent_no_crash(self) -> None:
        router, event_bus, _, _ = _make_router(agent=None)

        await router.start()
        inbound = _inbound("hello")
        # Should not raise
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(message=inbound, session_id="test:private:c1"),
                source="test",
            )
        )
        await router.stop()

    async def test_runtime_reasoning_display_override(self) -> None:
        class _ReasoningAgent(_MockAgentLoop):
            async def run_stream(self, **kwargs: Any) -> Any:
                self.calls.append(kwargs)
                yield LoopEvent(type="text", text="answer", reasoning="hidden steps")
                yield LoopEvent(
                    type="done",
                    final_response="answer",
                    reasoning="hidden steps",
                )

        memory = _MockMemoryStore()
        memory.session_meta["test:private:c1"] = {
            "runtime": {"reasoning": {"show": True}}
        }
        agent = _ReasoningAgent(response="answer")
        router, event_bus, channel_registry, _ = _make_router(
            agent=agent,
            memory=memory,
            config=RouterConfig(show_reasoning=False),
        )

        await router.start()
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(
                    message=_inbound("show reasoning"),
                    session_id="",
                ),
                source="test",
            )
        )
        await router.stop()

        channel = channel_registry.get("test")
        assert isinstance(channel, _StubChannel)
        assert channel.sent[0][1].reasoning == "hidden steps"

    async def test_registered_tools_are_passed_to_agent(self) -> None:
        async def _tool_handler(query: str) -> str:
            return f"result: {query}"

        registry = ToolRegistry()
        registry.register(
            ToolEntry(
                name="search",
                description="Search memory",
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                handler=_tool_handler,
                plugin_id="tool-plugin",
            )
        )
        agent = _MockAgentLoop(response="agent says hi")
        router, event_bus, _, _ = _make_router(agent=agent, tool_registry=registry)

        await router.start()
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(message=_inbound("use search"), session_id=""),
                source="test",
            )
        )
        await router.stop()

        tools = agent.calls[0]["tools"]
        assert len(tools) == 1
        assert tools[0].name == "search"
        assert tools[0].parameters["required"] == ["query"]

    async def test_active_workspace_root_is_passed_to_agent(
        self, tmp_path: Path
    ) -> None:
        manager = WorkspaceManager(tmp_path)
        manager.initialize()
        agent = _MockAgentLoop(response="agent says hi")
        router, event_bus, _, _ = _make_router(
            agent=agent,
            workspace_manager=manager,
        )

        await router.start()
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(
                    message=_inbound("read workspace"), session_id=""
                ),
                source="test",
            )
        )
        await router.stop()

        assert agent.calls[0]["workspace_root"] == manager.workspace_path("default")


class TestMessageRouterAgentStop:
    """AgentStopRequested event + /new cancellation of in-flight runs."""

    async def test_agent_stop_requested_sets_stop_event(self) -> None:
        """Publishing AgentStopRequested signals the active run's stop_event."""
        router, event_bus, _, _ = _make_router()
        await router.start()
        runner = cast(Any, router)._runner
        stop_event = asyncio.Event()
        task = asyncio.create_task(asyncio.sleep(60))
        runner.run_tracker.start("test:private:c1", task, stop_event)
        try:
            assert not stop_event.is_set()
            await event_bus.publish(
                AgentStopRequested(
                    payload=AgentStopPayload(session_id="test:private:c1"),
                    source="test",
                )
            )
            assert stop_event.is_set()
            # Unknown session is a no-op (request_stop returns False), no error.
            result = await event_bus.publish(
                AgentStopRequested(
                    payload=AgentStopPayload(session_id="test:private:other"),
                    source="test",
                )
            )
            assert not result.failures
        finally:
            runner.run_tracker.finish("test:private:c1")
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await router.stop()

    async def test_set_active_session_cancels_inflight_old_run(self) -> None:
        """/new switching session stops a run still in flight on the old one."""
        router, _, _, _ = _make_router()
        await router.start()
        runner = cast(Any, router)._runner
        address = ChatAddress(channel="test", target_type="private", target_id="c1")
        key = address.chat_key
        # The chat's current active session is "old" (e.g. from a prior /new),
        # and a run is in flight on it.
        cast(Any, router)._active_sessions[key] = "test:private:old"
        old_stop = asyncio.Event()
        old_task = asyncio.create_task(asyncio.sleep(60))
        runner.run_tracker.start("test:private:old", old_task, old_stop)
        try:
            assert not old_stop.is_set()
            router.set_active_session(address, "test:private:new")
            # Old session's run was signalled to stop, and the override moved.
            assert old_stop.is_set()
            assert cast(Any, router)._active_sessions[key] == "test:private:new"
        finally:
            runner.run_tracker.finish("test:private:old")
            old_task.cancel()
            await asyncio.gather(old_task, return_exceptions=True)
            await router.stop()

    async def test_set_active_session_does_not_cancel_when_no_active_run(self) -> None:
        """Switching to a new session is a no-op when nothing is running."""
        router, _, _, _ = _make_router()
        await router.start()
        # No run started; this must not raise or request any stop.
        router.set_active_session(
            ChatAddress(channel="test", target_type="private", target_id="c1"),
            "test:private:new",
        )
        await router.stop()

    async def test_stop_abort_requests_stop_then_cancels_straggler(self) -> None:
        """Abort shutdown signals stop_event, then cancels a task that ignores it."""
        router, _, _, _ = _make_router()
        await router.start()
        runner = cast(Any, router)._runner
        stop_event = asyncio.Event()
        task = asyncio.create_task(asyncio.sleep(60))
        runner.run_tracker.start("test:private:c1", task, stop_event)

        try:
            await router.stop(mode="abort", abort_timeout_seconds=0.01)

            assert stop_event.is_set()
            assert task.cancelled()
        finally:
            runner.run_tracker.finish("test:private:c1")
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_stop_drain_can_be_promoted_to_abort_by_abort_event(self) -> None:
        """Second Ctrl+C can promote shutdown drain into graceful run abort."""
        router, _, _, _ = _make_router()
        await router.start()
        runner = cast(Any, router)._runner
        stop_event = asyncio.Event()

        async def _worker() -> None:
            await stop_event.wait()

        task = asyncio.create_task(_worker())
        runner.run_tracker.start("test:private:c1", task, stop_event)
        abort_event = asyncio.Event()
        stop_task = asyncio.create_task(
            router.stop(
                mode="drain",
                abort_event=abort_event,
                abort_timeout_seconds=1.0,
            )
        )

        try:
            await asyncio.sleep(0)
            assert not stop_task.done()

            abort_event.set()
            await asyncio.wait_for(stop_task, timeout=1.0)

            assert stop_event.is_set()
            assert task.done()
            assert not task.cancelled()
        finally:
            runner.run_tracker.finish("test:private:c1")
            task.cancel()
            await asyncio.gather(task, stop_task, return_exceptions=True)


class TestMessageRouterAgentRunLifecycle:
    """AgentRunStarted/Cancelled/Finished are published per run."""

    async def _drain_nowait(self, event_bus: EventBus) -> None:
        if event_bus._pending_tasks:  # type: ignore[attr-defined]
            await asyncio.gather(
                *event_bus._pending_tasks,
                return_exceptions=True,  # type: ignore[attr-defined]
            )

    async def test_normal_run_emits_started_then_finished(self) -> None:
        agent = _MockAgentLoop(response="done")
        router, event_bus, _, _ = _make_router(agent=agent)
        seen: list[str] = []

        async def _capture(event: Any, ctx: Any) -> None:
            if isinstance(event, AgentRunStarted):
                seen.append("started")
            elif isinstance(event, AgentRunCancelled):
                seen.append("cancelled")
            elif isinstance(event, AgentRunFinished):
                seen.append(f"finished:{event.payload.terminal}")

        event_bus.subscribe(AgentRunStarted, _capture, priority=10)
        event_bus.subscribe(AgentRunCancelled, _capture, priority=10)
        event_bus.subscribe(AgentRunFinished, _capture, priority=10)

        await router.start()
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(
                    message=_inbound("hi"), session_id="test:private:c1"
                ),
                source="test",
            )
        )
        await router.stop()
        await self._drain_nowait(event_bus)

        assert seen == ["started", "finished:completed"]

    async def test_provider_error_done_emits_failed_finished(self) -> None:
        class _ProviderErrorAgent:
            async def run_stream(self, **kwargs: Any) -> Any:
                yield LoopEvent(
                    type="done",
                    final_response="Service temporarily unavailable.",
                    error="provider_auth_failed",
                )

        router, event_bus, _, _ = _make_router(agent=_ProviderErrorAgent())
        seen: list[tuple[str, str]] = []

        async def _capture(event: Any, ctx: Any) -> None:
            if isinstance(event, AgentRunFinished):
                seen.append((event.payload.terminal, event.payload.error))

        event_bus.subscribe(AgentRunFinished, _capture, priority=10)

        await router.start()
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(
                    message=_inbound("hi"), session_id="test:private:c1"
                ),
                source="test",
            )
        )
        await router.stop()
        await self._drain_nowait(event_bus)

        assert seen == [("failed", "provider_auth_failed")]


class TestMessageRouterMemory:
    async def test_history_loaded_from_memory(self) -> None:
        memory = _MockMemoryStore()
        await memory.ensure_session("test:private:c1")
        await memory.append_turn(
            "test:private:c1",
            ConversationTurn(role="user", content="hi", source="user_input"),
        )

        agent = _MockAgentLoop()
        router, event_bus, _, _ = _make_router(agent=agent, memory=memory)

        await router.start()
        inbound = _inbound("follow-up")
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(message=inbound, session_id="test:private:c1"),
                source="test",
            )
        )
        await router.stop()

        assert len(agent.calls) == 1
        history = agent.calls[0]["history_messages"]
        assert len(history) == 1
        assert history[0].content == "hi"

    async def test_active_session_override_uses_new_session_history(self) -> None:
        memory = _MockMemoryStore()
        await memory.ensure_session("test:private:c1")
        await memory.append_turn(
            "test:private:c1",
            ConversationTurn(role="user", content="old session", source="user_input"),
        )
        await memory.ensure_session("test:private:c1:new")

        agent = _MockAgentLoop()
        router, event_bus, _, _ = _make_router(agent=agent, memory=memory)
        router.set_active_session(
            ChatAddress(channel="test", target_type="private", target_id="c1"),
            "test:private:c1:new",
        )

        await router.start()
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(message=_inbound("fresh start"), session_id=""),
                source="test",
            )
        )
        await router.stop()

        assert len(agent.calls) == 1
        assert agent.calls[0]["history_messages"] == []
        assert len(memory.sessions["test:private:c1"]) == 1
        assert len(memory.sessions["test:private:c1:new"]) == 2

    async def test_memory_session_is_bound_to_active_workspace(
        self, tmp_path: Path
    ) -> None:
        manager = WorkspaceManager(tmp_path)
        manager.initialize()
        memory = _MockMemoryStore()
        agent = _MockAgentLoop()
        router, event_bus, _, _ = _make_router(
            agent=agent,
            memory=memory,
            workspace_manager=manager,
        )

        await router.start()
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(message=_inbound("hello"), session_id=""),
                source="test",
            )
        )
        await router.stop()

        assert memory.workspace_ids["test:private:c1"] == "default"

    async def test_turns_persisted_after_agent_run(self) -> None:
        memory = _MockMemoryStore()
        agent = _MockAgentLoop(response="answer")
        router, event_bus, _, _ = _make_router(agent=agent, memory=memory)

        await router.start()
        inbound = _inbound("question")
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(message=inbound, session_id="test:private:c1"),
                source="test",
            )
        )
        await router.stop()

        turns = memory.sessions["test:private:c1"]
        assert len(turns) == 2
        assert turns[0].role == "user"
        assert turns[0].content == "question"
        assert turns[0].metadata is not None
        assert turns[0].metadata["message_context"]["channel"] == "test"
        assert turns[0].metadata["message_context"]["sender_id"] == "u1"
        assert turns[1].role == "assistant"
        assert turns[1].content == "answer"

    async def test_assistant_envelope_output_is_cleaned_before_persist(self) -> None:
        memory = _MockMemoryStore()
        agent = _MockAgentLoop(
            response="[2026-05-10 14:03 +08 | test/private:c1 | u1]\nanswer"
        )
        router, event_bus, channel_registry, _ = _make_router(
            agent=agent,
            memory=memory,
        )

        await router.start()
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(
                    message=_inbound("question"),
                    session_id="test:private:c1",
                ),
                source="test",
            )
        )
        await router.stop()

        channel = channel_registry.get("test")
        assert isinstance(channel, _StubChannel)
        assert channel.sent[0][1].text == "answer"

        turns = memory.sessions["test:private:c1"]
        assert turns[1].role == "assistant"
        assert turns[1].content == "answer"

    async def test_observed_group_message_is_persisted_without_agent_run(self) -> None:
        from nahida_bot.plugins.base import ChatContext

        memory = _MockMemoryStore()
        agent = _MockAgentLoop(response="answer")
        router, event_bus, _, _ = _make_router(agent=agent, memory=memory)

        await router.start()
        inbound = InboundMessage(
            message_id="1",
            platform="test",
            chat_id="c1",
            user_id="u1",
            text="nearby context",
            raw_event={},
            is_group=True,
            chat_context=ChatContext(platform="test", chat_type="group"),
        )
        await event_bus.publish(
            MessageObserved(
                payload=MessagePayload(message=inbound, session_id="test:group:c1"),
                source="test",
            )
        )
        await router.stop()

        assert agent.calls == []
        turns = memory.sessions["test:group:c1"]
        assert len(turns) == 1
        assert turns[0].source == "group_observation"
        assert turns[0].metadata is not None
        assert turns[0].metadata["observed_only"] is True
        assert turns[0].metadata["triggered_agent"] is False

    async def test_persist_observed_message_records_target_metadata(self) -> None:
        memory = _MockMemoryStore()
        runner = SessionRunner(memory_store=memory)

        await runner.persist_observed_message(
            inbound=InboundMessage(
                message_id="m1",
                platform="test",
                chat_id="c1",
                user_id="u1",
                text="nearby context",
                raw_event={},
                is_group=True,
                mentions_bot=True,
                mentioned_user_ids=("bot-1",),
            ),
            session_id="test:private:c1",
            workspace_id="default",
        )

        turns = memory.sessions["test:private:c1"]
        assert len(turns) == 1
        assert memory.workspace_ids["test:private:c1"] == "default"
        assert turns[0].metadata is not None
        assert turns[0].metadata["observed_only"] is True
        assert turns[0].metadata["triggered_agent"] is False
        assert turns[0].metadata["mentions_bot"] is True
        assert turns[0].metadata["mentioned_user_ids"] == ["bot-1"]
        assert turns[0].metadata["message_context"]["chat_type"] == "group"

    async def test_observed_group_context_is_injected_on_trigger(self) -> None:
        memory = _MockMemoryStore()
        agent = _MockAgentLoop(response="answer")
        router, event_bus, _, _ = _make_router(agent=agent, memory=memory)

        await router.start()
        await event_bus.publish(
            MessageObserved(
                payload=MessagePayload(
                    message=InboundMessage(
                        message_id="1",
                        platform="test",
                        chat_id="c1",
                        user_id="u1",
                        text="Alice mentioned the deployment",
                        raw_event={},
                        is_group=True,
                    ),
                    session_id="test:private:c1",
                ),
                source="test",
            )
        )
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(
                    message=InboundMessage(
                        message_id="2",
                        platform="test",
                        chat_id="c1",
                        user_id="u2",
                        text="@bot summarize",
                        raw_event={},
                        is_group=True,
                        mentions_bot=True,
                    ),
                    session_id="test:private:c1",
                ),
                source="test",
            )
        )
        await router.stop()

        assert len(agent.calls) == 1
        history = agent.calls[0]["history_messages"]
        observed = [m for m in history if m.source == "group_observed_context"]
        assert len(observed) == 1
        assert "Alice mentioned the deployment" in observed[0].content
        assert memory.get_recent_calls == 1
        assert all(
            not (
                isinstance(message.metadata, dict)
                and message.metadata.get("observed_only") is True
            )
            for message in history
        )

    async def test_observed_context_skips_duplicate_current_trigger(self) -> None:
        memory = _MockMemoryStore()
        agent = _MockAgentLoop(response="answer")
        router, event_bus, _, _ = _make_router(agent=agent, memory=memory)

        await router.start()
        await event_bus.publish(
            MessageObserved(
                payload=MessagePayload(
                    message=InboundMessage(
                        message_id="1",
                        platform="test",
                        chat_id="c1",
                        user_id="u1",
                        text="same message",
                        raw_event={},
                        is_group=True,
                        timestamp=123.0,
                    ),
                    session_id="test:private:c1",
                ),
                source="test",
            )
        )
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(
                    message=InboundMessage(
                        message_id="1",
                        platform="test",
                        chat_id="c1",
                        user_id="u1",
                        text="same message",
                        raw_event={},
                        is_group=True,
                        timestamp=123.0,
                        mentions_bot=True,
                    ),
                    session_id="test:private:c1",
                ),
                source="test",
            )
        )
        await router.stop()

        assert len(agent.calls) == 1
        history = agent.calls[0]["history_messages"]
        assert [m for m in history if m.source == "group_observed_context"] == []

    async def test_set_active_session_persists_override(self) -> None:
        memory = _MockMemoryStore()
        router, _, _, _ = _make_router(memory=memory)

        router.set_active_session(
            ChatAddress(channel="test", target_type="private", target_id="c1"),
            "test:private:c1:abc",
        )

        # Fire-and-forget persistence needs a loop tick to complete
        await asyncio.sleep(0)
        assert memory.persisted_overrides == {"test:private:c1": "test:private:c1:abc"}

    async def test_active_session_restored_on_start(self) -> None:
        memory = _MockMemoryStore()
        memory.persisted_overrides["test:private:c1"] = "test:private:c1:xyz"

        agent = _MockAgentLoop()
        router, _event_bus, _, _ = _make_router(agent=agent, memory=memory)

        # Override was NOT set via set_active_session — only in persisted storage
        assert (
            router.get_active_session_id(
                ChatAddress(channel="test", target_type="private", target_id="c1")
            )
            == "test:private:c1"
        )

        await router.start()
        # After start, the persisted override should be loaded
        assert (
            router.get_active_session_id(
                ChatAddress(channel="test", target_type="private", target_id="c1")
            )
            == "test:private:c1:xyz"
        )
        await router.stop()

    async def test_restored_session_used_for_message_dispatch(self) -> None:
        memory = _MockMemoryStore()
        await memory.ensure_session("test:private:c1")
        await memory.append_turn(
            "test:private:c1",
            ConversationTurn(role="user", content="old", source="user_input"),
        )
        await memory.ensure_session("test:private:c1:restored")
        memory.persisted_overrides["test:private:c1"] = "test:private:c1:restored"

        agent = _MockAgentLoop()
        router, event_bus, _, _ = _make_router(agent=agent, memory=memory)

        await router.start()
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(message=_inbound("hello"), session_id=""),
                source="test",
            )
        )
        await router.stop()

        # Agent should have been called with the restored session's (empty) history
        assert len(agent.calls) == 1
        assert agent.calls[0]["history_messages"] == []


class TestGroupDialogueContinuity:
    """Issue #37: group dialogue history is bounded to the current conversation
    segment via a time-gap continuity gate, so stale dialogue from a different
    conversation is dropped while the fresh observed window is retained."""

    @staticmethod
    def _record(
        role: str,
        content: str,
        created_at: datetime,
        *,
        observed: bool = False,
    ) -> MemoryRecord:
        metadata = {"observed_only": True} if observed else None
        return MemoryRecord(
            turn_id=0,
            session_id="test:group:c1",
            turn=ConversationTurn(
                role=role,
                content=content,
                metadata=metadata,
                created_at=created_at,
            ),
        )

    def _cutoff(
        self,
        records: list[MemoryRecord],
        *,
        timestamp: float,
        chat_type: str = "group",
        gap: int = 1800,
    ) -> datetime | None:
        runner = SessionRunner(group_context_continuity_gap_seconds=gap)
        ctx = MessageContext(
            chat_type=chat_type, chat_id="c1", channel="test", timestamp=timestamp
        )
        return runner._group_dialogue_cutoff(records, message_context=ctx)

    def _topic_cutoff(
        self,
        records: list[MemoryRecord],
        *,
        timestamp: float,
        gap: int = 300,
    ) -> datetime | None:
        runner = SessionRunner(group_context_topic_gap_seconds=gap)
        ctx = MessageContext(
            chat_type="group", chat_id="c1", channel="test", timestamp=timestamp
        )
        return runner._group_topic_cutoff(records, message_context=ctx)

    def test_disabled_gap_returns_none(self) -> None:
        now = datetime.now(UTC)
        records = [self._record("user", "hi", now - timedelta(hours=2))]
        assert self._cutoff(records, timestamp=now.timestamp(), gap=0) is None

    def test_private_chat_returns_none(self) -> None:
        now = datetime.now(UTC)
        records = [self._record("user", "hi", now - timedelta(hours=2))]
        assert (
            self._cutoff(records, timestamp=now.timestamp(), chat_type="private")
            is None
        )

    def test_no_prior_dialogue_returns_none(self) -> None:
        now = datetime.now(UTC)
        records = [
            self._record(
                "user", "ambient chatter", now - timedelta(seconds=60), observed=True
            )
        ]
        assert self._cutoff(records, timestamp=now.timestamp()) is None

    def test_stale_dialogue_all_dropped_when_gap_exceeds_threshold(self) -> None:
        now = datetime.now(UTC)
        stale = now - timedelta(hours=2)
        records = [
            self._record("user", "stale question", stale),
            self._record("assistant", "stale answer", stale + timedelta(seconds=5)),
        ]
        cutoff = self._cutoff(records, timestamp=now.timestamp())
        assert cutoff is not None
        # New conversation → every prior dialogue turn falls below the cutoff.
        assert all(record.turn.created_at < cutoff for record in records)

    def test_recent_dialogue_kept_within_gap(self) -> None:
        now = datetime.now(UTC)
        recent = now - timedelta(minutes=5)
        records = [
            self._record("user", "recent question", recent),
            self._record("assistant", "recent answer", recent + timedelta(seconds=5)),
        ]
        cutoff = self._cutoff(records, timestamp=now.timestamp())
        assert cutoff is not None
        assert all(record.turn.created_at >= cutoff for record in records)

    def test_internal_segment_boundary_drops_older_segment(self) -> None:
        now = datetime.now(UTC)
        old_segment = now - timedelta(hours=3)
        current_segment = now - timedelta(minutes=5)  # >30min gap before it
        records = [
            self._record("user", "old segment q", old_segment),
            self._record(
                "assistant", "old segment a", old_segment + timedelta(seconds=5)
            ),
            self._record("user", "current segment q", current_segment),
            self._record(
                "assistant", "current segment a", current_segment + timedelta(seconds=5)
            ),
        ]
        cutoff = self._cutoff(records, timestamp=now.timestamp())
        assert cutoff is not None
        kept = [r.turn.content for r in records if r.turn.created_at >= cutoff]
        assert "current segment q" in kept
        assert "current segment a" in kept
        assert "old segment q" not in kept
        assert "old segment a" not in kept

    def test_topic_gap_starts_fresh_automatic_context(self) -> None:
        now = datetime.now(UTC)
        records = [
            self._record("user", "old ambient topic", now - timedelta(minutes=10))
        ]

        cutoff = self._topic_cutoff(records, timestamp=now.timestamp())

        assert cutoff == now
        assert records[0].turn.created_at < cutoff

    def test_topic_gap_keeps_only_latest_ambient_segment(self) -> None:
        now = datetime.now(UTC)
        latest_segment = now - timedelta(minutes=2)
        records = [
            self._record("user", "old ambient topic", now - timedelta(minutes=12)),
            self._record("user", "new topic starts", latest_segment),
            self._record("user", "new topic continues", now - timedelta(minutes=1)),
        ]

        cutoff = self._topic_cutoff(records, timestamp=now.timestamp())

        assert cutoff == latest_segment
        kept = [r.turn.content for r in records if r.turn.created_at >= cutoff]
        assert kept == ["new topic starts", "new topic continues"]

    async def test_explicit_reply_restores_anchor_outside_topic_window(self) -> None:
        memory = _MockMemoryStore()
        old_at = datetime.now(UTC) - timedelta(minutes=10)
        memory.sessions["test:group:c1"] = [
            ConversationTurn(
                role="user",
                content="the exact old message being discussed",
                source="group_observation",
                created_at=old_at,
                metadata={
                    "observed_only": True,
                    "message_id": "old-message",
                    "message_context": {
                        "timestamp": old_at.timestamp(),
                        "channel": "test",
                        "chat_type": "group",
                        "chat_id": "c1",
                        "sender_id": "u2",
                        "sender_display_name": "Alice",
                        "message_id": "old-message",
                    },
                },
            )
        ]
        agent = _MockAgentLoop(response="reply")
        router, event_bus, _, _ = _make_router(agent=agent, memory=memory)

        await router.start()
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(
                    message=InboundMessage(
                        message_id="current-message",
                        platform="test",
                        chat_id="c1",
                        user_id="u1",
                        text="what do you think about this?",
                        raw_event={},
                        is_group=True,
                        reply_to="old-message",
                        timestamp=datetime.now(UTC).timestamp(),
                    ),
                    session_id="test:group:c1",
                ),
                source="test",
            )
        )
        await router.stop()

        history = agent.calls[0]["history_messages"]
        anchors = [m for m in history if m.source == "reply_anchor_context"]
        assert len(anchors) == 1
        assert "the exact old message being discussed" in anchors[0].content
        assert anchors[0].metadata["message_id"] == "old-message"
        assert [m for m in history if m.source == "group_observed_context"] == []

    async def test_stale_group_dialogue_dropped_on_trigger(self) -> None:
        memory = _MockMemoryStore()
        stale_at = datetime.now(UTC) - timedelta(hours=2)
        memory.sessions["test:group:c1"] = [
            ConversationTurn(
                role="user",
                content="stale question from hours ago",
                source="user_input",
                created_at=stale_at,
            ),
            ConversationTurn(
                role="assistant",
                content="stale answer from hours ago",
                source="agent",
                created_at=stale_at + timedelta(seconds=5),
            ),
        ]
        agent = _MockAgentLoop(response="fresh reply")
        router, event_bus, _, _ = _make_router(agent=agent, memory=memory)

        await router.start()
        # Recent observed chatter inside the observed-context TTL window.
        await event_bus.publish(
            MessageObserved(
                payload=MessagePayload(
                    message=InboundMessage(
                        message_id="o1",
                        platform="test",
                        chat_id="c1",
                        user_id="u2",
                        text="someone chatting recently",
                        raw_event={},
                        is_group=True,
                    ),
                    session_id="test:group:c1",
                ),
                source="test",
            )
        )
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(
                    message=InboundMessage(
                        message_id="t1",
                        platform="test",
                        chat_id="c1",
                        user_id="u1",
                        text="@bot now please",
                        raw_event={},
                        is_group=True,
                        mentions_bot=True,
                        timestamp=datetime.now(UTC).timestamp(),
                    ),
                    session_id="test:group:c1",
                ),
                source="test",
            )
        )
        await router.stop()

        assert len(agent.calls) == 1
        history = agent.calls[0]["history_messages"]
        contents = [message.content or "" for message in history]
        # Stale dialogue from a different conversation is dropped from history.
        assert not any("stale question" in c for c in contents)
        assert not any("stale answer" in c for c in contents)
        # Fresh observed chatter is still injected as group context.
        observed = [m for m in history if m.source == "group_observed_context"]
        assert len(observed) == 1
        assert "someone chatting recently" in observed[0].content


class TestMessageRouterSentinel:
    """Sentinel token suppression in the streaming reply path."""

    async def test_no_reply_suppresses_send(self) -> None:
        agent = _MockAgentLoop(response="NO_REPLY")
        router, event_bus, channel_registry, _ = _make_router(agent=agent)
        channel = channel_registry.get("test")
        assert channel is not None

        await router.start()
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(message=_inbound("hello"), session_id=""),
                source="test",
            )
        )
        await router.stop()

        assert isinstance(channel, _StubChannel)
        assert len(channel.sent) == 0

    async def test_heartbeat_ok_suppresses_send(self) -> None:
        agent = _MockAgentLoop(response="HEARTBEAT_OK")
        router, event_bus, channel_registry, _ = _make_router(agent=agent)
        channel = channel_registry.get("test")
        assert channel is not None

        await router.start()
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(message=_inbound("check"), session_id=""),
                source="test",
            )
        )
        await router.stop()

        assert isinstance(channel, _StubChannel)
        assert len(channel.sent) == 0

    async def test_trailing_no_reply_sends_remaining(self) -> None:
        agent = _MockAgentLoop(response="Summary of results\nNO_REPLY")
        router, event_bus, channel_registry, _ = _make_router(agent=agent)
        channel = channel_registry.get("test")
        assert channel is not None

        await router.start()
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(message=_inbound("report"), session_id=""),
                source="test",
            )
        )
        await router.stop()

        assert isinstance(channel, _StubChannel)
        assert len(channel.sent) == 1
        assert channel.sent[0][1].text == "Summary of results"

    async def test_sentinel_disabled_sends_raw(self) -> None:
        agent = _MockAgentLoop(response="NO_REPLY")
        config = RouterConfig(enable_silent_reply=False)
        router, event_bus, channel_registry, _ = _make_router(
            agent=agent, config=config
        )
        channel = channel_registry.get("test")
        assert channel is not None

        await router.start()
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(message=_inbound("hello"), session_id=""),
                source="test",
            )
        )
        await router.stop()

        assert isinstance(channel, _StubChannel)
        assert len(channel.sent) == 1
        assert channel.sent[0][1].text == "NO_REPLY"

    async def test_json_envelope_no_reply_suppresses(self) -> None:
        agent = _MockAgentLoop(response='{"action": "NO_REPLY"}')
        router, event_bus, channel_registry, _ = _make_router(agent=agent)
        channel = channel_registry.get("test")
        assert channel is not None

        await router.start()
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(message=_inbound("hello"), session_id=""),
                source="test",
            )
        )
        await router.stop()

        assert isinstance(channel, _StubChannel)
        assert len(channel.sent) == 0
