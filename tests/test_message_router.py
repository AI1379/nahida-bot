"""Tests for MessageRouter."""

from __future__ import annotations

import asyncio
from pathlib import Path
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
from nahida_bot.core.session_runner import SessionRunner
from nahida_bot.plugins.base import InboundMessage, OutboundMessage, Plugin
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
    return InboundMessage(
        message_id="1",
        platform=platform,
        chat_id="c1",
        user_id="u1",
        text=text,
        raw_event={},
    )


class _StubChannel(Plugin):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._channel_id = self.manifest.id
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


class _MockMemoryStore:
    """Minimal MemoryStore mock."""

    def __init__(self) -> None:
        self.sessions: dict[str, list[ConversationTurn]] = {}
        self.workspace_ids: dict[str, str | None] = {}
        self.persisted_overrides: dict[str, str] = {}

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

    async def persist_active_session(self, chat_key: str, session_id: str) -> None:
        self.persisted_overrides[chat_key] = session_id

    async def load_active_sessions(self) -> dict[str, str]:
        return dict(self.persisted_overrides)


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
    tool_registry: ToolRegistry | None = None,
    workspace_manager: WorkspaceManager | None = None,
    config: RouterConfig | None = None,
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
                    session_id="test:c1",
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
                payload=MessagePayload(message=inbound, session_id="test:c1"),
                source="test",
            )
        )
        await router.stop()

        handler.assert_awaited_once()
        assert handler.call_args.kwargs["args"] == "hello world"

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

    async def test_active_session_override_uses_new_session_history(self) -> None:
        memory = _MockMemoryStore()
        await memory.ensure_session("test:c1")
        await memory.append_turn(
            "test:c1",
            ConversationTurn(role="user", content="old session", source="user_input"),
        )
        await memory.ensure_session("test:c1:new")

        agent = _MockAgentLoop()
        router, event_bus, _, _ = _make_router(agent=agent, memory=memory)
        router.set_active_session("test", "c1", "test:c1:new")

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
        assert len(memory.sessions["test:c1"]) == 1
        assert len(memory.sessions["test:c1:new"]) == 2

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

        assert memory.workspace_ids["test:c1"] == "default"

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

    async def test_set_active_session_persists_override(self) -> None:
        memory = _MockMemoryStore()
        router, _, _, _ = _make_router(memory=memory)

        router.set_active_session("test", "c1", "test:c1:abc")

        # Fire-and-forget persistence needs a loop tick to complete
        await asyncio.sleep(0)
        assert memory.persisted_overrides == {"test:c1": "test:c1:abc"}

    async def test_active_session_restored_on_start(self) -> None:
        memory = _MockMemoryStore()
        memory.persisted_overrides["test:c1"] = "test:c1:xyz"

        agent = _MockAgentLoop()
        router, event_bus, _, _ = _make_router(agent=agent, memory=memory)

        # Override was NOT set via set_active_session — only in persisted storage
        assert router.get_active_session_id("test", "c1") == "test:c1"

        await router.start()
        # After start, the persisted override should be loaded
        assert router.get_active_session_id("test", "c1") == "test:c1:xyz"
        await router.stop()

    async def test_restored_session_used_for_message_dispatch(self) -> None:
        memory = _MockMemoryStore()
        await memory.ensure_session("test:c1")
        await memory.append_turn(
            "test:c1",
            ConversationTurn(role="user", content="old", source="user_input"),
        )
        await memory.ensure_session("test:c1:restored")
        memory.persisted_overrides["test:c1"] = "test:c1:restored"

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
