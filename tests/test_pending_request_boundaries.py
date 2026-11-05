"""Issue #66: request membership, scoped social views and queue isolation.

Scripted appraisals exercise the real routing/context pipeline without relying
on a model to follow instructions. Agents, channels and storage are test doubles.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nahida_bot.core.events import MessagePayload, MessageReceived, MessageSent
from nahida_bot.core.message_context import context_from_inbound
from nahida_bot.plugins.conversation_joiner.social import SocialTurn
from nahida_bot.plugins.registry import PromptSupplementEntry, PromptSupplementRegistry
from nahida_bot_sdk.messaging import AgentContextRequest

from .test_conversation_joiner_plugin import (
    _JoinerAPI,
    _drain_plugin_tasks,
    _load_plugin,
)
from .test_message_router import _make_router, _MockMemoryStore
from .test_pending_message_batch import (
    SESSION_ID,
    _BlockingAgent,
    _finish_runs,
    _message,
)


def _appraisal(*message_ids: str, task_focused: bool = False) -> str:
    return json.dumps(
        {
            "social": {
                "scene": {
                    "text": "A conversation event",
                    "evidence_ids": list(message_ids),
                },
                "thoughts": [],
                "task_focused": task_focused,
            }
        }
    )


def _social_data(call):
    snapshots = [
        message
        for message in call["history_messages"]
        if message.source == "plugin_context:test:social"
    ]
    if not snapshots:
        return None
    assert len(snapshots) == 1
    text = snapshots[0].content
    return json.loads(text[text.index("{") :])


@pytest.fixture
async def boundary_case(request):
    api = _JoinerAPI([])
    plugin = await _load_plugin(
        api,
        {
            "inner_thoughts": {
                "enabled": getattr(request, "param", True),
                "direct_update_interval_seconds": 0,
            }
        },
    )
    agent, memory = _BlockingAgent(block_second=True), _MockMemoryStore()
    router, bus, channels, _ = _make_router(agent=agent, memory=memory)
    context_requests = []

    async def context_provider(context_request):
        context_requests.append(context_request)
        return await plugin._social_context(context_request)

    registry = PromptSupplementRegistry()
    registry.register(
        PromptSupplementEntry(
            "test:social", "", "test", context_provider=context_provider
        )
    )
    router._runner._supplement_registry = registry

    async def forward_plugin_event(event, _context):
        for handler in api.registered_event_handlers[type(event)]:
            await handler(event)
        await _drain_plugin_tasks(plugin)

    for event_type in (MessageReceived, MessageSent):
        bus.subscribe(event_type, forward_plugin_event, priority=1)

    await router.start()
    try:
        yield SimpleNamespace(
            api=api,
            plugin=plugin,
            agent=agent,
            router=router,
            bus=bus,
            channels=channels,
            memory=memory,
            context_requests=context_requests,
        )
    finally:
        for release in agent.release:
            release.set()
        await router.stop()
        await bus.shutdown(timeout=3)
        await plugin.on_unload()


async def _publish(bus, message):
    result = await bus.publish(
        MessageReceived(
            payload=MessagePayload(message=message, session_id=SESSION_ID),
            source="test",
        )
    )
    assert not result.failures


async def _start_backlog(case, *messages):
    await _publish(case.bus, _message("busy", "initial request", user_id="owner"))
    await asyncio.wait_for(case.agent.started[0].wait(), timeout=3)
    for message in messages:
        await _publish(case.bus, message)
    case.agent.release[0].set()
    await asyncio.wait_for(case.agent.started[1].wait(), timeout=3)


@pytest.mark.parametrize("senders", [("alice", "bob"), ("alice", "bob", "alice")])
async def test_distinct_senders_keep_fifo_identity_and_one_dispatch(
    boundary_case, senders
):
    case = boundary_case
    messages = [
        replace(_message(f"m{i}", f"request {i}", user_id=user), mentions_bot=True)
        for i, user in enumerate(senders, 1)
    ]
    await _start_backlog(case, *messages)
    assert [item.inbound.message_id for item in case.router._pending[SESSION_ID]] == [
        message.message_id for message in messages[1:]
    ]
    case.agent.release[1].set()
    await _finish_runs(case.router)

    calls = case.agent.calls
    assert len(calls) == len(messages) + 1
    assert [
        request.message_context.message_id for request in case.context_requests
    ] == [
        "busy",
        *[message.message_id for message in messages],
    ]
    assert [call["sender_account_key"] for call in calls[1:]] == [
        f"test:user:{sender}" for sender in senders
    ]
    for call, message in zip(calls[1:], messages, strict=True):
        assert f"message_id: {message.message_id}\n" in call["user_message"]
        assert 'role="batch_message"' not in call["user_message"]
    users = [turn for turn in case.memory.sessions[SESSION_ID] if turn.role == "user"]
    assert [turn.metadata["message_id"] for turn in users] == [
        "busy",
        *[message.message_id for message in messages],
    ]
    assert len(case.channels.get("test").sent) == len(calls)


@pytest.mark.parametrize("boundary_case", [False, True], indirect=True)
@pytest.mark.parametrize("include_unassigned", [False, True])
async def test_later_request_stays_queued_and_cannot_enter_current_snapshot(
    boundary_case, include_unassigned
):
    case = boundary_case
    # Script only the appraisal. The main agent never infers or executes a draw.
    evidence_ids = ("m1", "m2") if include_unassigned else ("m1",)
    case.api.responses[:] = ["{}", _appraisal(*evidence_ids, task_focused=True)]
    current = _message("m1", "draw for Alice", user_id="alice")
    later = _message("m2", "draw for Bob", user_id="bob")
    await _start_backlog(case, current, later)

    call = case.agent.calls[1]
    assert "draw for Bob" not in call["user_message"]
    assert call["sender_account_key"] == "test:user:alice"
    request = case.context_requests[1]
    assert request.input_messages == (current,)
    assert request.actor_account_key == "test:user:alice"
    assert request.background_message_ids == frozenset({"busy"})
    assert [item.inbound.message_id for item in case.router._pending[SESSION_ID]] == [
        "m2"
    ]
    if case.plugin._social is None:
        assert _social_data(call) is None
        assert case.api.llm_calls == []
    else:
        data = _social_data(call)
        if include_unassigned:
            assert data is None
        else:
            assert data["task_focused"] is True
            assert data["thoughts"] == [] and data["feeling"] is None
            assert {item["message_id"] for item in data["evidence"]} == {"m1"}
        prompt = case.api.llm_calls[1]["messages"][1]["content"]
        assert 'Active request message IDs: ["m1"]' in prompt
        assert 'role="background"' in prompt
        assert 'role="current_request"' in prompt
        assert "message_id: m2\n" not in prompt
        assert "draw for Bob" not in prompt
    assert case.api.agent_response_requests == []

    case.agent.release[1].set()
    await _finish_runs(case.router)
    assert len(case.agent.calls) == 3
    assert "draw for Bob" in case.agent.calls[2]["user_message"]
    assert case.agent.calls[2]["sender_account_key"] == "test:user:bob"


@pytest.mark.parametrize("anchor", ["first", "last"])
async def test_context_provider_receives_original_batch_and_one_reply_anchor(
    boundary_case, anchor
):
    case = boundary_case
    first = replace(
        _message("m1", "original", user_id="alice"), mentions_bot=anchor == "first"
    )
    last = replace(
        _message("m2", "correction", user_id="alice"), mentions_bot=anchor == "last"
    )
    await _start_backlog(case, first, last)

    request = case.context_requests[1]
    assert request.user_message == "original\n\ncorrection"
    assert request.message_context.message_id == ("m1" if anchor == "first" else "m2")
    assert request.message_context.sender_id == "alice"
    assert request.input_messages == (first, last)
    assert request.actor_account_key == "test:user:alice"
    assert case.agent.calls[1]["user_message"].count('role="batch_message"') == 2
    prompt = case.api.llm_calls[1]["messages"][1]["content"]
    assert 'Active request message IDs: ["m1", "m2"]' in prompt
    recent_chat = prompt.split("\nRecent chat:\n", 1)[1].split(
        "\nContinuity data:\n", 1
    )[0]
    assert recent_chat.count(first.text) == recent_chat.count(last.text) == 1
    case.agent.release[1].set()
    await _finish_runs(case.router)
    assert len(case.agent.calls) == 2


@pytest.mark.parametrize("anchor", ["first", "last"])
async def test_batch_social_evidence_keeps_each_original_message_body(
    boundary_case, anchor
):
    case = boundary_case
    first = replace(
        _message("m1", "original", user_id="alice"), mentions_bot=anchor == "first"
    )
    last = replace(
        _message("m2", "correction", user_id="alice"), mentions_bot=anchor == "last"
    )
    await _start_backlog(case, first, last)
    social = case.plugin._social
    key = social._key(SESSION_ID, SESSION_ID)
    evidence = {item.message_id: item.text for item in social._states[key].evidence}
    assert {key: evidence[key] for key in ("m1", "m2")} == {
        "m1": first.text,
        "m2": last.text,
    }


@pytest.mark.parametrize(
    "refresh", ["rate_limit", "invalid", "provider_error", "timeout", "retain"]
)
async def test_retained_state_is_projected_on_every_fallback_path(
    boundary_case, refresh
):
    case = boundary_case
    later = _message("later", "draw for Bob", user_id="bob")
    past = _message("past", "a historical greeting", user_id="friend")
    seed = json.loads(_appraisal("later"))
    seed["social"]["feeling"] = {
        "text": "A friendly greeting",
        "label": "curious",
        "intensity": 0.4,
        "evidence_ids": ["past"],
    }
    case.api.responses[:] = [json.dumps(seed)]
    social = case.plugin._social
    await social.chat(
        SocialTurn(SESSION_ID, SESSION_ID, "", (past, later), later.message_id),
        system="test gate",
        prompt="test gate",
        model="cheap",
    )
    current = _message("current", "draw for Alice", user_id="alice")
    request = AgentContextRequest(
        SESSION_ID,
        "user_input",
        current.text,
        context_from_inbound(current),
        background_message_ids=frozenset({"past"}),
    )
    assert "draw for Bob" in await social.snapshot(SESSION_ID, SESSION_ID, "")

    # Simulate fixing only selection from the rolling recent-input buffer.
    # Durable short-lived social state is intentionally still present.
    case.plugin._contexts.clear()
    if refresh == "rate_limit":
        case.plugin._social.config = case.plugin._social.config.model_copy(
            update={"direct_update_interval_seconds": 300}
        )
    elif refresh == "invalid":
        case.api.responses[:] = ['{"social":{"thoughts":"invalid"}}']
    elif refresh == "provider_error":
        case.api.llm_chat = AsyncMock(side_effect=RuntimeError("appraisal unavailable"))
    elif refresh == "timeout":
        case.plugin._config = case.plugin._config.model_copy(
            update={"decision_timeout_seconds": 0.1}
        )
        cancelled = asyncio.Event()

        async def timeout_appraisal(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        case.api.llm_chat = AsyncMock(side_effect=timeout_appraisal)
    else:
        case.api.responses[:] = ['{"social":{"scene":null,"thoughts":[]}}']

    snapshot = await case.plugin._social_context(request)
    assert "draw for Bob" not in snapshot and '"message_id": "later"' not in snapshot
    data = json.loads(snapshot[snapshot.index("{") :])
    assert data["scene"] is None
    assert data["feeling"]["text"] == "A friendly greeting"
    assert {e["message_id"] for e in data["evidence"]} == {"past"}
    # Projection leaves unrelated shared state available to the join gate.
    assert "draw for Bob" in await social.snapshot(SESSION_ID, SESSION_ID, "")
    if refresh == "rate_limit":
        assert len(case.api.llm_calls) == 1
    elif refresh == "timeout":
        assert cancelled.is_set()
    elif refresh not in {"provider_error", "timeout"}:
        prompt = case.api.llm_calls[1]["messages"][1]["content"]
        recent_chat = prompt.split("\nRecent chat:\n", 1)[1].split(
            "\nContinuity data:\n", 1
        )[0]
        assert "draw for Bob" not in recent_chat
        assert "draw for Bob" not in prompt.split("\nContinuity data:\n", 1)[1]


async def test_concurrent_appraisal_cannot_replace_a_direct_reply_snapshot(
    boundary_case,
):
    """The reply view is captured before a waiting gate can change shared state."""
    case = boundary_case
    current = _message("current", "draw for Alice", user_id="alice")
    later = _message("later", "draw for Bob", user_id="bob")
    case.api.responses[:] = [_appraisal("current"), _appraisal("later")]
    entered, release, gate_waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_chat = case.api.llm_chat

    async def block_first_appraisal(*args, **kwargs):
        if not entered.is_set():
            entered.set()
            await release.wait()
        return await original_chat(*args, **kwargs)

    case.api.llm_chat = block_first_appraisal
    request = AgentContextRequest(
        SESSION_ID, "user_input", current.text, context_from_inbound(current)
    )
    direct = asyncio.create_task(case.plugin._social_context(request))
    gate = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)

        async def appraise_later():
            gate_waiting.set()
            return await case.plugin._social.chat(
                SocialTurn(SESSION_ID, SESSION_ID, "", (later,), later.message_id),
                system="test gate",
                prompt="test gate",
                model="cheap",
            )

        gate = asyncio.create_task(appraise_later())
        await asyncio.wait_for(gate_waiting.wait(), timeout=3)
        release.set()
        snapshot = await asyncio.wait_for(direct, timeout=3)
        await asyncio.wait_for(gate, timeout=3)
        assert "draw for Alice" in snapshot
        assert "draw for Bob" not in snapshot
        assert "draw for Bob" in await case.plugin._social.snapshot(
            SESSION_ID, SESSION_ID, ""
        )
        assert "draw for Bob" not in case.api.llm_calls[0]["messages"][1]["content"]
        assert len(case.api.llm_calls) == 2
    finally:
        release.set()
        await asyncio.wait_for(
            asyncio.gather(direct, *([gate] if gate else [])), timeout=3
        )


async def test_context_request_preserves_authenticated_actor_and_original_input(
    boundary_case,
):
    case = boundary_case
    original = _message("current", "hello", user_id="visible-sender")
    result = await case.bus.publish(
        MessageReceived(
            payload=MessagePayload(
                message=original,
                session_id=SESSION_ID,
                actor_account_key="test:user:authenticated-owner",
            ),
            source="test",
        )
    )
    assert not result.failures
    await asyncio.wait_for(case.agent.started[0].wait(), timeout=3)
    request = case.context_requests[0]
    assert request.input_messages == (original,)
    assert request.message_context.sender_id == "visible-sender"
    assert request.actor_account_key == "test:user:authenticated-owner"
    assert case.agent.calls[0]["sender_account_key"] == request.actor_account_key


async def test_waiting_for_social_lock_times_out_without_reading_unscoped_state(
    boundary_case,
):
    case = boundary_case
    case.plugin._config = case.plugin._config.model_copy(
        update={"decision_timeout_seconds": 0.1}
    )
    message = _message("current", "hello", user_id="alice")
    request = AgentContextRequest(
        SESSION_ID, "user_input", message.text, context_from_inbound(message)
    )
    social = case.plugin._social
    lock = social._lock(social._key(SESSION_ID, SESSION_ID))
    async with lock:
        assert (
            await asyncio.wait_for(case.plugin._social_context(request), timeout=3)
            == ""
        )
    assert case.api.llm_calls == []


async def test_error_fallback_reconciles_the_original_body_for_a_repeated_id(
    boundary_case,
):
    case = boundary_case
    stale = _message("current", "a stale combined body", user_id="alice")
    social = case.plugin._social
    case.api.responses[:] = [_appraisal("current")]
    await social.chat(
        SocialTurn(SESSION_ID, SESSION_ID, "", (stale,), stale.message_id),
        system="test gate",
        prompt="test gate",
        model="cheap",
    )
    case.api.llm_chat = AsyncMock(side_effect=RuntimeError("appraisal unavailable"))
    original = replace(stale, text="the actual original body")
    request = AgentContextRequest(
        SESSION_ID,
        "user_input",
        original.text,
        context_from_inbound(original),
        input_messages=(original,),
    )
    snapshot = await case.plugin._social_context(request)
    assert original.text in snapshot
    assert stale.text not in snapshot


async def test_batch_keeps_all_current_inputs_when_background_budget_is_small(
    boundary_case,
):
    case = boundary_case
    case.plugin._config = case.plugin._config.model_copy(
        update={"max_context_messages": 1}
    )
    messages = [_message(f"m{i}", f"input-{i}", user_id="alice") for i in range(3)]
    await _start_backlog(case, *messages)
    assert case.context_requests[1].input_messages == tuple(messages)
    prompt = case.api.llm_calls[1]["messages"][1]["content"]
    recent_chat = prompt.split("\nRecent chat:\n", 1)[1].split(
        "\nContinuity data:\n", 1
    )[0]
    for message in messages:
        assert f"message_id: {message.message_id}\n" in recent_chat
        assert recent_chat.count(message.text) == 1
    assert "initial request" not in recent_chat
