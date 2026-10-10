"""Steering admission, interruption, history, and per-message identity."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from nahida_bot.agent.context import ContextMessage
from nahida_bot.core.config import PendingMessagesConfig
from nahida_bot.core.context import current_session
from nahida_bot.core.events import MessagePayload, MessageReceived
from nahida_bot.core.router import PendingMessage, RouterConfig
from nahida_bot.plugins.base import ChatContext
from nahida_bot.plugins.builtin.commands import BuiltinCommandsPlugin
from nahida_bot.plugins.commands import CommandEntry
from nahida_bot.plugins.manifest import PluginManifest
from tests.test_message_router import (
    _inbound,
    _make_router,
    _MockAgentLoop,
    _MockMemoryStore,
)


class _HeldAgent(_MockAgentLoop):
    """Keep the first run alive after stop so queue handoff can be inspected."""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.contexts: list[Any] = []

    async def run_stream(self, **kwargs: Any) -> Any:
        from nahida_bot.agent.loop import LoopEvent

        self.calls.append(kwargs)
        self.contexts.append(current_session.get())
        if len(self.calls) == 1:
            self.started.set()
            yield LoopEvent(type="text", text="partial reply")
            await kwargs["stop_event"].wait()
            await self.release.wait()
            yield LoopEvent(
                type="done",
                error="cancelled",
                final_response="partial reply",
                assistant_messages=[
                    ContextMessage(
                        role="assistant",
                        content="partial reply",
                        source="agent_response",
                    )
                ],
            )
        else:
            yield LoopEvent(type="done", final_response="new reply")


@asynccontextmanager
async def _running_router(agent=None, *, config=None, mute_service=None):
    memory = _MockMemoryStore()
    router, bus, channels, commands = _make_router(
        agent=agent or _MockAgentLoop(),
        memory=memory,
        config=config,
        mute_service=mute_service,
    )
    api = MagicMock()
    api.message_router = router

    def register(name, handler, **kwargs):
        commands.register(
            CommandEntry(
                name=name,
                handler=handler,
                description=kwargs.get("description", ""),
                aliases=tuple(kwargs.get("aliases", ())),
                arguments=tuple(kwargs.get("arguments", ())),
                plugin_id="builtin-commands",
            )
        )

    api.register_command = register
    plugin = BuiltinCommandsPlugin(
        api=api,
        manifest=PluginManifest(
            id="builtin-commands", name="Builtin", version="1", entrypoint="x:X"
        ),
    )
    plugin._register_commands()
    await router.start()
    try:
        yield router, bus, channels.get("test"), memory, commands
    finally:
        if isinstance(agent, _HeldAgent):
            agent.release.set()
        await router.stop(mode="abort", abort_timeout_seconds=0.1)
        await bus.shutdown(timeout=1)


async def _publish(bus, text, *, user="u1", group=False):
    inbound = replace(
        _inbound(text),
        message_id=f"{user}:{text}",
        user_id=user,
        is_group=group,
        chat_context=ChatContext(
            platform="test", chat_type="group" if group else "private"
        ),
    )
    result = await bus.publish(
        MessageReceived(
            payload=MessagePayload(message=inbound, session_id=""), source="test"
        )
    )
    assert not result.failures


async def _wait_idle(router):
    async def wait_runs():
        while runs := router._runner.run_tracker.all_runs:
            await asyncio.gather(*(run.task for run in runs))

    await asyncio.wait_for(wait_runs(), timeout=2)


async def test_steer_prioritizes_prompt_preserves_history_and_sender_context():
    agent = _HeldAgent()
    async with _running_router(agent) as (router, bus, _, memory, _):
        await _publish(bus, "original task", user="owner", group=True)
        await asyncio.wait_for(agent.started.wait(), timeout=1)
        old_run = router._runner.run_tracker.get("test:group:c1")
        await _publish(bus, "queued one", user="u3", group=True)
        await _publish(bus, "queued two", user="u4", group=True)
        await _publish(bus, "/steer new direction", user="member", group=True)

        assert old_run.stop_event.is_set()
        assert not old_run.task.done()
        assert [p.inbound.text for p in router._pending["test:group:c1"]] == [
            "new direction",
            "queued one",
            "queued two",
        ]
        agent.release.set()
        await _wait_idle(router)

        assert not old_run.task.cancelled()
        assert len(agent.calls) == 4
        assert [
            turn.content
            for turn in memory.sessions["test:group:c1"]
            if turn.role == "user"
        ] == ["original task", "new direction", "queued one", "queued two"]
        history = agent.calls[1]["history_messages"]
        assert any("original task" in item.content for item in history)
        assert any("partial reply" in item.content for item in history)
        assert [ctx.actor_account_key for ctx in agent.contexts] == [
            "test:user:owner",
            "test:user:member",
            "test:user:u3",
            "test:user:u4",
        ]
        assert agent.calls[1]["sender_account_key"] == "test:user:member"
        assert all(call["session_id"] == "test:group:c1" for call in agent.calls)


async def test_idle_steer_submits_multiline_prompt_as_literal_text():
    agent = _MockAgentLoop()
    async with _running_router(agent) as (router, bus, _, memory, commands):
        entry = commands.get("steer")
        assert entry.arguments[0].name == "prompt"
        assert entry.arguments[0].required
        await _publish(bus, "/steer /stop\nexplain this command")
        await _wait_idle(router)
        assert len(agent.calls) == 1
        assert (
            "text:\n  /stop\n  explain this command" in agent.calls[0]["user_message"]
        )
        assert memory.sessions["test:private:c1"][0].content == (
            "/stop\nexplain this command"
        )


async def test_repeated_steer_runs_latest_prompt_first_without_discarding_inputs():
    agent = _HeldAgent()
    async with _running_router(agent) as (router, bus, _, memory, _):
        await _publish(bus, "original")
        await asyncio.wait_for(agent.started.wait(), timeout=1)
        await _publish(bus, "/steer first correction")
        await _publish(bus, "/steer second correction")
        agent.release.set()
        await _wait_idle(router)
        assert [
            t.content for t in memory.sessions["test:private:c1"] if t.role == "user"
        ] == ["original", "second correction", "first correction"]


async def test_group_steer_remains_separate_from_pending_message_batch():
    agent = _HeldAgent()
    config = RouterConfig(pending_messages=PendingMessagesConfig(mode="batch"))
    async with _running_router(agent, config=config) as (router, bus, _, memory, _):
        await _publish(bus, "original", group=True)
        await asyncio.wait_for(agent.started.wait(), timeout=1)
        await _publish(bus, "queued one", group=True)
        await _publish(bus, "queued two", group=True)
        await _publish(bus, "/steer new direction", group=True)
        agent.release.set()
        await _wait_idle(router)
        assert len(agent.calls) == 3
        assert "new direction" in agent.calls[1]["user_message"]
        assert "queued one" not in agent.calls[1]["user_message"]
        assert "new direction" not in agent.calls[2]["user_message"]
        assert "queued one" in agent.calls[2]["user_message"]
        assert "queued two" in agent.calls[2]["user_message"]
        assert [
            t.content for t in memory.sessions["test:group:c1"] if t.role == "user"
        ] == ["original", "new direction", "queued one", "queued two"]


@pytest.mark.parametrize("command", ["/steer", "/steer   "])
async def test_empty_steer_does_not_interrupt_active_run(command):
    agent = _HeldAgent()
    async with _running_router(agent) as (router, bus, channel, _, _):
        await _publish(bus, "original")
        await asyncio.wait_for(agent.started.wait(), timeout=1)
        await _publish(bus, command)
        assert not router._runner.run_tracker.get("test:private:c1").stop_event.is_set()
        assert not router._pending
        assert channel.sent[-1][1].text == "Usage: /steer <prompt>"


async def test_full_queue_rejects_steer_without_stopping_or_dropping_messages():
    agent = _HeldAgent()
    config = RouterConfig(pending_messages=PendingMessagesConfig(max_messages=1))
    async with _running_router(agent, config=config) as (router, bus, channel, _, _):
        await _publish(bus, "original")
        await asyncio.wait_for(agent.started.wait(), timeout=1)
        await _publish(bus, "already queued")
        await _publish(bus, "/steer rejected")
        assert not router._runner.run_tracker.get("test:private:c1").stop_event.is_set()
        assert [p.inbound.text for p in router._pending["test:private:c1"]] == [
            "already queued"
        ]
        assert "queue is full" in channel.sent[-1][1].text


async def test_expired_messages_behind_steering_prompt_are_removed():
    async with _running_router() as (router, _, _, _, _):
        now = time.monotonic()
        router._pending["test:private:c1"] = deque(
            [
                PendingMessage(
                    inbound=_inbound("steer"), workspace_id=None, queued_at=now
                ),
                PendingMessage(
                    inbound=_inbound("expired"), workspace_id=None, queued_at=now - 1000
                ),
                PendingMessage(
                    inbound=_inbound("fresh"), workspace_id=None, queued_at=now
                ),
            ]
        )
        router._expire_pending("test:private:c1")
        assert [p.inbound.text for p in router._pending["test:private:c1"]] == [
            "steer",
            "fresh",
        ]


async def test_muted_chat_rejects_steer():
    mute_service = MagicMock()
    mute_service.status = AsyncMock(return_value=MagicMock(muted=True, expired=False))
    async with _running_router(mute_service=mute_service) as (
        router,
        bus,
        channel,
        _,
        _,
    ):
        await _publish(bus, "/steer new direction")
        assert not router._pending
        assert not router._runner.run_tracker.all_runs
        assert "Chat is muted" in channel.sent[-1][1].text


@pytest.mark.parametrize("unavailable", ["no_agent", "disabled", "shutting_down"])
async def test_unavailable_agent_rejects_steer(unavailable):
    async with _running_router() as (router, bus, channel, _, _):
        if unavailable == "no_agent":
            router.agent = None
        elif unavailable == "disabled":
            router._config.agent_enabled = False
        else:
            router._stopping = True
        await _publish(bus, "/steer new direction")
        assert not router._pending
        assert not router._runner.run_tracker.all_runs
        assert "not available" in channel.sent[-1][1].text or (
            "shutting down" in channel.sent[-1][1].text
        )


async def test_steer_interrupts_inflight_model_request_and_starts_next_run():
    from nahida_bot.agent.context import ContextBudget, ContextBuilder
    from nahida_bot.agent.loop import AgentLoop
    from nahida_bot.agent.providers.base import ProviderResponse
    from nahida_bot.agent.tokenization import CharacterEstimateTokenizer
    from tests.test_agent_loop import _BlockingProvider

    class InterruptProvider(_BlockingProvider):
        calls = 0

        async def _chat_impl(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return await super()._chat_impl(**kwargs)
            return ProviderResponse(content="steered answer", tool_calls=[])

    provider = InterruptProvider()
    agent = AgentLoop(
        provider=provider,
        context_builder=ContextBuilder(
            budget=ContextBudget(max_tokens=1000, reserved_tokens=0),
            fallback_tokenizer=CharacterEstimateTokenizer(chars_per_token=20),
        ),
    )
    async with _running_router(agent) as (router, bus, channel, memory, _):
        await _publish(bus, "original")
        await asyncio.wait_for(provider.chat_started.wait(), timeout=1)
        await _publish(bus, "/steer new direction")
        await _wait_idle(router)
        assert provider.cancelled
        assert provider.calls == 2
        assert any(msg.text == "steered answer" for _, msg in channel.sent)
        assert [
            t.content for t in memory.sessions["test:private:c1"] if t.role == "user"
        ] == ["original", "new direction"]
