"""Group backlog batching, actor isolation, media, and persistence contracts."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from nahida_bot.agent.context import ContextPart
from nahida_bot.agent.loop import LoopEvent
from nahida_bot.agent.providers.base import ModelCapabilities
from nahida_bot.core.config import PendingMessagesConfig, Settings
from nahida_bot.core.context import SessionContext, current_attachments
from nahida_bot.core.events import MessagePayload, MessageReceived
from nahida_bot.core.message_batch import take_pending_batch
from nahida_bot.core.message_context import context_from_inbound
from nahida_bot.core.router import PendingMessage, RouterConfig
from nahida_bot.core.session_runner import SessionRunner
from nahida_bot.plugins.base import ChatContext, InboundAttachment
from nahida_bot.plugins.commands import CommandMatcher

from .test_message_router import (
    _inbound,
    _make_router,
    _MockAgentLoop,
    _MockMemoryStore,
)

SESSION_ID = "test:group:c1"


def _message(message_id: str, text: str = "question", user_id: str = "u1"):
    return replace(
        _inbound(text),
        message_id=message_id,
        user_id=user_id,
        is_group=True,
        chat_context=ChatContext(platform="test", chat_type="group"),
    )


def _pending(message_id: str, text: str = "question", **kwargs):
    return PendingMessage(_message(message_id, text), "workspace", **kwargs)


def test_settings_default_to_batch_and_allow_sequential():
    assert Settings().router.pending_messages.mode == "batch"
    config = Settings.model_validate(
        {"router": {"pending_messages": {"mode": "sequential"}}}
    )
    assert config.router.pending_messages.mode == "sequential"
    for invalid in (
        {"mode": "unknown"},
        {"batch_max_messages": 0},
        {"batch_max_chars": 0},
    ):
        with pytest.raises(ValidationError):
            PendingMessagesConfig.model_validate(invalid)


@pytest.mark.parametrize(
    "boundary",
    [
        {"inbound": _message("other", user_id="u2")},
        {"inbound": replace(_message("other"), is_group=False)},
        {"inbound": _message("other", "/unrecognized_command")},
        {"inbound": replace(_message("other"), user_id="")},
        {"workspace_id": "other"},
        {"source_tag": "proactive_join"},
        {"source_tag": "node"},
        {"parse_commands": False},
        {"batchable": False},
        {"agent_instruction": "different instructions"},
        {
            "session_context": SessionContext(
                "test", "c1", SESSION_ID, credential_id="other"
            )
        },
    ],
)
def test_batch_stops_at_boundary_without_reordering(boundary):
    first = _pending("first")
    other = replace(_pending("other"), **boundary)
    last = _pending("last")
    queue = deque([first, other, last])
    assert take_pending_batch(queue, PendingMessagesConfig(), CommandMatcher()) == (
        first,
    )
    assert list(queue) == [other, last]


@pytest.mark.parametrize(
    "config",
    [
        PendingMessagesConfig(batch_max_messages=2),
        PendingMessagesConfig(batch_max_chars=4),
    ],
)
def test_batch_is_bounded_and_keeps_remainder(config):
    messages = [_pending(str(i), "ab") for i in range(3)]
    queue = deque(messages)
    assert take_pending_batch(queue, config, CommandMatcher()) == tuple(messages[:2])
    assert list(queue) == messages[2:]


def test_oversized_input_is_not_truncated_or_dropped():
    first, second = _pending("first", "x" * 20), _pending("second")
    queue = deque([first, second])
    assert take_pending_batch(
        queue, PendingMessagesConfig(batch_max_chars=2), CommandMatcher()
    ) == (first,)
    assert list(queue) == [second]


class _BlockingAgent(_MockAgentLoop):
    def __init__(self, block_second=False):
        super().__init__()
        self.started = [asyncio.Event(), asyncio.Event()]
        self.release = [asyncio.Event(), asyncio.Event()]
        self.block_second = block_second
        self.attachment_snapshots = []

    async def run_stream(self, **kwargs):
        index = len(self.calls)
        self.calls.append(kwargs)
        self.attachment_snapshots.append(current_attachments.get())
        if index == 0 or (index == 1 and self.block_second):
            self.started[index].set()
            await self.release[index].wait()
        yield LoopEvent(type="done", final_response="answer")


async def _publish(bus, message):
    await bus.publish(
        MessageReceived(
            payload=MessagePayload(message=message, session_id=SESSION_ID),
            source="test",
        )
    )


async def _finish_runs(router):
    while run := router._runner.run_tracker.get(SESSION_ID):
        await asyncio.wait_for(asyncio.shield(run.task), timeout=3)


async def test_busy_group_messages_share_run_and_preserve_originals():
    agent, memory = _BlockingAgent(), _MockMemoryStore()
    router, bus, channels, _ = _make_router(agent=agent, memory=memory)
    first = _message("m1", "use this", user_id="bob")
    first = replace(
        first,
        mentions_bot=True,
        reply_to="original",
        attachments=[
            InboundAttachment(
                kind="file", platform_id="file1", url="https://example.com/one"
            )
        ],
    )
    second = replace(
        _message("m2", "correction: use that", user_id="bob"),
        attachments=[
            InboundAttachment(
                kind="file", platform_id="file2", url="https://example.com/two"
            )
        ],
    )
    await router.start()
    try:
        await _publish(bus, _message("m0", user_id="admin"))
        await asyncio.wait_for(agent.started[0].wait(), 3)
        await _publish(bus, first)
        await _publish(bus, second)
        agent.release[0].set()
        await _finish_runs(router)
        assert len(agent.calls) == 2
        call = agent.calls[1]
        assert call["sender_account_key"] == "test:user:bob"
        assert call["user_message"].index("use this") < call["user_message"].index(
            "correction: use that"
        )
        assert call["user_message"].count('role="batch_message"') == 2
        assert "original" in call["user_message"]
        assert [a.platform_id for a in agent.attachment_snapshots[1]] == [
            "file1",
            "file2",
        ]
        users = [turn for turn in memory.sessions[SESSION_ID] if turn.role == "user"]
        assert [turn.metadata["message_id"] for turn in users] == ["m0", "m1", "m2"]
        assert [turn.content for turn in users[1:]] == [first.text, second.text]
        for turn, media_id in zip(users[1:], ["file1", "file2"]):
            assert turn.metadata["input_batch_message_ids"] == ["m1", "m2"]
            assert turn.metadata["attachments"][0]["platform_id"] == media_id
        assert channels.get("test").sent[-1][1].reply_to == "m1"
    finally:
        agent.release[0].set()
        await router.stop()


async def test_sequential_mode_retains_one_run_per_input():
    agent = _BlockingAgent()
    router, bus, _, _ = _make_router(
        agent=agent,
        config=RouterConfig(pending_messages=PendingMessagesConfig(mode="sequential")),
    )
    await router.start()
    try:
        await _publish(bus, _message("m0"))
        await asyncio.wait_for(agent.started[0].wait(), 3)
        await _publish(bus, _message("m1", "first"))
        await _publish(bus, _message("m2", "second"))
        agent.release[0].set()
        await _finish_runs(router)
        assert len(agent.calls) == 3
        assert all(
            'role="batch_message"' not in call["user_message"] for call in agent.calls
        )
    finally:
        agent.release[0].set()
        await router.stop()


async def test_messages_arriving_after_batch_start_wait_for_next_run():
    agent = _BlockingAgent(block_second=True)
    router, bus, _, _ = _make_router(agent=agent)
    await router.start()
    try:
        await _publish(bus, _message("m0"))
        await asyncio.wait_for(agent.started[0].wait(), 3)
        await _publish(bus, _message("m1", "first"))
        await _publish(bus, _message("m2", "second"))
        agent.release[0].set()
        await asyncio.wait_for(agent.started[1].wait(), 3)
        await _publish(bus, _message("m3", "arrived later"))
        agent.release[1].set()
        await _finish_runs(router)
        assert len(agent.calls) == 3
        assert "arrived later" not in agent.calls[1]["user_message"]
        assert "arrived later" in agent.calls[2]["user_message"]
    finally:
        for release in agent.release:
            release.set()
        await router.stop()


async def test_losing_dispatch_race_preserves_age_and_steer_priority():
    router, _, _, _ = _make_router(
        config=RouterConfig(pending_messages=PendingMessagesConfig(max_messages=1))
    )
    router._runner = SimpleNamespace(
        has_agent=True, run_tracker=SimpleNamespace(is_active=lambda _: True)
    )
    router._check_chat_mute = AsyncMock(return_value=False)
    batch = (_pending("m1"), _pending("m2"))
    steer = _pending("steer", parse_commands=False)
    router._pending[SESSION_ID] = deque([steer])
    await router._dispatch_message(
        batch[-1].inbound, SESSION_ID, "workspace", pending_batch=batch
    )
    assert list(router._pending[SESSION_ID]) == [steer, *batch]


async def test_group_steer_runs_before_and_separately_from_backlog_batch():
    agent = _BlockingAgent()
    router, bus, _, _ = _make_router(agent=agent)
    await router.start()
    try:
        await _publish(bus, _message("m0"))
        await asyncio.wait_for(agent.started[0].wait(), 3)
        await _publish(bus, _message("m1", "first"))
        await _publish(bus, _message("m2", "second"))
        result = await router.steer_message("new focus", _message("steer"), SESSION_ID)
        assert "queued" in result
        agent.release[0].set()
        await _finish_runs(router)
        assert len(agent.calls) == 3
        assert "new focus" in agent.calls[1]["user_message"]
        assert 'role="batch_message"' not in agent.calls[1]["user_message"]
        assert agent.calls[2]["user_message"].count('role="batch_message"') == 2
        assert "new focus" not in agent.calls[2]["user_message"]
    finally:
        agent.release[0].set()
        await router.stop()


async def test_muted_batch_preserves_all_observed_messages():
    router, _, _, _ = _make_router(agent=_MockAgentLoop())
    router._check_chat_mute = AsyncMock(return_value=True)
    router._runner.persist_observed_message = AsyncMock()
    batch = (_pending("m1"), _pending("m2"))
    router._pending[SESSION_ID] = deque(batch)
    await router._drain_pending(SESSION_ID)
    observed = router._runner.persist_observed_message.await_args_list
    assert [call.kwargs["inbound"].message_id for call in observed] == ["m1", "m2"]


async def test_batch_media_stays_with_message_and_shares_image_limit():
    runner = SessionRunner()
    messages = tuple(
        replace(
            _message(f"m{i}"),
            attachments=[InboundAttachment(kind="image", platform_id=f"image{i}")],
        )
        for i in range(2)
    )

    async def build_parts(text, attachments, **kwargs):
        return [ContextPart(type="text", text=text)] + [
            ContextPart(type="image_url", media_id=a.platform_id) for a in attachments
        ]

    runner._build_user_parts = AsyncMock(side_effect=build_parts)
    parts = await runner._build_batch_user_parts(
        messages, capabilities=ModelCapabilities(image_input=True, max_image_count=1)
    )
    assert [part.type for part in parts] == [
        "text",
        "text",
        "image_url",
        "text",
        "text",
    ]
    assert parts[2].media_id == "image0"
    assert "m0" in parts[1].text and "m1" in parts[3].text
    assert "cannot see" in parts[-1].text


async def test_cancelled_batch_still_persists_every_input():
    class CancelledAgent:
        async def run_stream(self, **kwargs):
            yield LoopEvent(type="done", error="cancelled")

    memory = _MockMemoryStore()
    runner = SessionRunner(agent_loop=CancelledAgent(), memory_store=memory)
    messages = (_message("m1", "one"), _message("m2", "two"))
    await runner.run(
        user_message="two",
        session_id=SESSION_ID,
        system_prompt="test",
        message_context=context_from_inbound(messages[-1]),
        inbound_batch=messages,
    )
    turns = memory.sessions[SESSION_ID]
    assert [turn.content for turn in turns] == ["one", "two"]
