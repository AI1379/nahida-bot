"""Tests for MessageRouter."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from nahida_bot.agent.memory.models import ConversationTurn, MemoryRecord
from nahida_bot.core.channel_registry import ChannelRegistry
from nahida_bot.core.events import (
    EventBus,
    EventContext,
    MessageReceived,
    MessagePayload,
)
from nahida_bot.core.router import MessageRouter, RouterConfig
from nahida_bot.plugins.base import InboundMessage, OutboundMessage
from nahida_bot.plugins.channel_plugin import ChannelPlugin
from nahida_bot.plugins.commands import CommandEntry, CommandMatcher, CommandRegistry
from nahida_bot.plugins.manifest import PluginManifest


# ── Helpers ──────────────────────────────────────────────────


def _inbound(text: str = "hello", platform: str = "test") -> InboundMessage:
    return InboundMessage(
        message_id="1",
        platform=platform,
        chat_id="c1",
        user_id="u1",
        text=text,
        raw_event={},
    )


class _StubChannel(ChannelPlugin):
    async def on_load(self) -> None:
        pass

    async def handle_inbound_event(self, event: dict) -> None:
        pass

    async def send_message(self, target: str, message: OutboundMessage) -> str:
        return "msg_1"


class _MockMemoryStore:
    """Minimal MemoryStore mock."""

    def __init__(self) -> None:
        self.sessions: dict[str, list[ConversationTurn]] = {}

    async def ensure_session(
        self, session_id: str, workspace_id: str | None = None
    ) -> None:
        self.sessions.setdefault(session_id, [])

    async def append_turn(self, session_id: str, turn: ConversationTurn) -> int:
        self.sessions.setdefault(session_id, []).append(turn)
        return len(self.sessions[session_id])

    async def get_recent(
        self, session_id: str, *, limit: int = 50
    ) -> list[MemoryRecord]:
        turns = self.sessions.get(session_id, [])
        return [
            MemoryRecord(turn_id=i, session_id=session_id, turn=t)
            for i, t in enumerate(turns[-limit:])
        ]

    async def search(
        self, session_id: str, query: str, *, limit: int = 10
    ) -> list[MemoryRecord]:
        return []

    async def evict_before(self, cutoff: Any) -> int:
        return 0


class _MockAgentLoop:
    """Minimal AgentLoop mock."""

    def __init__(self, response: str = "agent reply") -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def run(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        result = MagicMock()
        result.final_response = self.response
        return result


def _make_router(
    *,
    agent: Any = None,
    memory: Any = None,
    config: RouterConfig | None = None,
) -> tuple[MessageRouter, EventBus, ChannelRegistry, CommandRegistry]:
    event_bus = EventBus(EventContext(app=None, settings=None, logger=MagicMock()))  # type: ignore[arg-type]
    command_registry = CommandRegistry()
    command_matcher = CommandMatcher()
    channel_registry = ChannelRegistry()

    # Register a stub channel for the "test" platform
    manifest = PluginManifest(
        id="test", name="Test", version="1.0", entrypoint="t:T", type="channel"
    )
    channel = _StubChannel(api=MagicMock(), manifest=manifest)
    channel_registry.register(channel)

    router = MessageRouter(
        event_bus=event_bus,
        command_registry=command_registry,
        command_matcher=command_matcher,
        channel_registry=channel_registry,
        agent_loop=agent,
        memory_store=memory,
        config=config,
    )
    return router, event_bus, channel_registry, command_registry


# ── Tests ────────────────────────────────────────────────────


class TestMessageRouterSessionId:
    def test_session_id_format(self) -> None:
        assert MessageRouter.make_session_id("telegram", "123") == "telegram:123"

    def test_session_id_deterministic(self) -> None:
        a = MessageRouter.make_session_id("qq", "456")
        b = MessageRouter.make_session_id("qq", "456")
        assert a == b


class TestMessageRouterCommandDispatch:
    async def test_command_match_dispatches_to_handler(self) -> None:
        router, event_bus, _, command_registry = _make_router()

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
                    session_id="test:c1",
                ),
                source="test",
            )
        )
        await router.stop()

        handler.assert_awaited_once()
        assert handler.call_args.kwargs["args"] == ""

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
                payload=MessagePayload(message=inbound, session_id="test:c1"),
                source="test",
            )
        )
        await router.stop()

        handler.assert_awaited_once()
        assert handler.call_args.kwargs["args"] == "hello world"


class TestMessageRouterAgentDispatch:
    async def test_no_command_dispatches_to_agent(self) -> None:
        agent = _MockAgentLoop(response="agent says hi")
        router, event_bus, _, _ = _make_router(agent=agent)

        await router.start()
        inbound = _inbound("what is 2+2?")
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(message=inbound, session_id="test:c1"),
                source="test",
            )
        )
        await router.stop()

        assert len(agent.calls) == 1
        assert agent.calls[0]["user_message"] == "what is 2+2?"

    async def test_no_agent_no_crash(self) -> None:
        router, event_bus, _, _ = _make_router(agent=None)

        await router.start()
        inbound = _inbound("hello")
        # Should not raise
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(message=inbound, session_id="test:c1"),
                source="test",
            )
        )
        await router.stop()


class TestMessageRouterMemory:
    async def test_history_loaded_from_memory(self) -> None:
        memory = _MockMemoryStore()
        await memory.ensure_session("test:c1")
        await memory.append_turn(
            "test:c1",
            ConversationTurn(role="user", content="hi", source="user_input"),
        )

        agent = _MockAgentLoop()
        router, event_bus, _, _ = _make_router(agent=agent, memory=memory)

        await router.start()
        inbound = _inbound("follow-up")
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(message=inbound, session_id="test:c1"),
                source="test",
            )
        )
        await router.stop()

        assert len(agent.calls) == 1
        history = agent.calls[0]["history_messages"]
        assert len(history) == 1
        assert history[0].content == "hi"

    async def test_turns_persisted_after_agent_run(self) -> None:
        memory = _MockMemoryStore()
        agent = _MockAgentLoop(response="answer")
        router, event_bus, _, _ = _make_router(agent=agent, memory=memory)

        await router.start()
        inbound = _inbound("question")
        await event_bus.publish(
            MessageReceived(
                payload=MessagePayload(message=inbound, session_id="test:c1"),
                source="test",
            )
        )
        await router.stop()

        turns = memory.sessions["test:c1"]
        assert len(turns) == 2
        assert turns[0].role == "user"
        assert turns[0].content == "question"
        assert turns[1].role == "assistant"
        assert turns[1].content == "answer"
