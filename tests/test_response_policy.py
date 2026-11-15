"""Failure delivery follows trusted triggers, without changing normal RP replies."""

import asyncio
from dataclasses import replace

import pytest

from nahida_bot.agent.context import ContextMessage
from nahida_bot.agent.loop import AgentRunResult, LoopEvent
from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.events import (
    AgentResponseRequested,
    AgentResponseRequestPayload,
    AgentRunFinished,
    MessageReceived,
    MessagePayload,
)
from nahida_bot.core.session_runner import SessionRunner
from nahida_bot.core.runtime_settings import runtime_settings_from_meta
from nahida_bot.plugins.base import ChatContext
from tests.test_message_router import _inbound, _make_router, _MockMemoryStore


@pytest.mark.parametrize(
    "trigger,mode,sends",
    [
        ("private", "interactive", True),
        ("mention", "interactive", True),
        ("always", "interactive", False),
        ("proactive_join", "interactive", False),
        ("proactive_join", "all", True),
        ("private", "none", False),
    ],
)
@pytest.mark.parametrize("crash", [False, True])
async def test_router_error_notifications(trigger, mode, sends, crash):
    class Agent:
        async def run_stream(self, **kwargs):
            if crash:
                raise RuntimeError("provider unavailable")
            yield LoopEvent(
                type="done",
                error="provider_unavailable",
                terminal_state="failed",
                final_response="fallback notice",
            )

    memory = _MockMemoryStore()
    group = trigger != "private"
    session = "test:group:c1" if group else "test:private:c1"
    memory.session_meta[session] = {"runtime": {"error_notifications": mode}}
    router, bus, channels, _ = _make_router(agent=Agent(), memory=memory)
    inbound = replace(
        _inbound(),
        is_group=group,
        mentions_bot=trigger == "mention",
        chat_context=ChatContext(
            platform="test", chat_type="group" if group else "private"
        ),
    )
    finished = []

    async def capture(event, ctx):
        finished.append(event.payload.terminal)

    bus.subscribe(AgentRunFinished, capture, priority=10)
    await router.start()
    if trigger == "proactive_join":
        await bus.publish(
            AgentResponseRequested(
                payload=AgentResponseRequestPayload(
                    message=inbound,
                    session_id=session,
                    chat_address=ChatAddress.parse(session),
                    requester_plugin_id="conversation_joiner",
                    reason="join discussion",
                ),
                source="conversation_joiner",
            )
        )
    else:
        await bus.publish(
            MessageReceived(
                payload=MessagePayload(message=inbound, session_id=session),
                source="test",
            )
        )
    await router.stop()
    if bus._pending_tasks:
        await asyncio.gather(*bus._pending_tasks)
    assert bool(channels.get("test").sent) == sends
    assert finished == ["crashed" if crash else "failed"]
    assert all(t.content != "fallback notice" for t in memory.sessions.get(session, []))


async def test_successful_proactive_reply_with_error_word_is_not_suppressed():
    from tests.test_message_router import _MockAgentLoop

    router, bus, channels, _ = _make_router(
        agent=_MockAgentLoop("This error is easy to fix.")
    )
    inbound = replace(_inbound(), is_group=True, chat_context=None)
    await router.start()
    await bus.publish(
        AgentResponseRequested(
            payload=AgentResponseRequestPayload(
                message=inbound,
                session_id="test:group:c1",
                chat_address=ChatAddress.parse("test:group:c1"),
                requester_plugin_id="conversation_joiner",
                reason="join",
            ),
            source="conversation_joiner",
        )
    )
    await router.stop()
    assert [m.text for _, m in channels.get("test").sent] == [
        "This error is easy to fix."
    ]


def test_failure_notice_is_not_consolidated_as_assistant_memory():
    runner = SessionRunner(agent_loop=None)
    result = AgentRunResult(
        final_response="Fallback template",
        error="failed",
        terminal_state="failed",
        assistant_messages=[
            ContextMessage(
                role="assistant", content="An earlier genuine reply", source="agent"
            )
        ],
    )
    turns = runner._assistant_visible_turns(result, include_message_context=False)
    assert [t.content for t in turns] == ["An earlier genuine reply"]


def test_runtime_settings_default_does_not_enable_background_errors():
    for value in (None, "invalid", True, 1):
        runtime = runtime_settings_from_meta(
            {"runtime": {"error_notifications": value}}
        )
        assert runtime.error_notifications == "interactive"


@pytest.mark.parametrize(
    "mode,sends", [("interactive", False), ("none", False), ("all", True)]
)
async def test_cron_failed_result_is_counted_and_never_sends_model_fallback(
    mode, sends
):
    from tests.test_scheduler import _repo, _job, _Channel, _make_service
    from nahida_bot.agent.memory.sqlite import SQLiteMemoryStore

    class Agent:
        async def run_stream(self, **kwargs):
            yield LoopEvent(
                type="done",
                terminal_state="failed",
                error="provider_unavailable",
                final_response="Do not send this fallback",
            )

    engine, repo = await _repo()
    try:
        job = _job()
        store = SQLiteMemoryStore(engine)
        await store.ensure_session(job.created_from_session_id or job.session_key)
        await store.set_error_notifications(
            job.created_from_session_id or job.session_key, mode
        )
        channel = _Channel()
        service = _make_service(engine, repo, agent=Agent(), channel=channel)
        service._runner.memory = store
        await repo.insert_job(job)
        await service._fire_job(job)
        stored = await repo.get_job(job.job_id)
        assert stored.failure_count == 1 and stored.run_count == 0
        assert bool(channel.sent) == sends
        assert all(
            "Do not send this fallback" not in message.text
            for _, message in channel.sent
        )
    finally:
        await engine.close()
