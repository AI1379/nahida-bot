"""Behavioral contracts for grounded appraisal, lifecycle, and cache placement."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from nahida_bot.agent.loop import LoopEvent
from nahida_bot.core.events import MessageSent, MessagePayload
from nahida_bot.core.message_context import context_from_inbound
from nahida_bot.core.session_runner import SessionRunner
from nahida_bot.plugins.conversation_joiner.config import InnerThoughtsConfig
from nahida_bot.plugins.conversation_joiner.social import SocialContinuity
from nahida_bot.plugins.conversation_joiner.social_state import (
    Evidence,
    Feeling,
    Note,
    SocialState,
    SocialUpdate,
    Thought,
)
from nahida_bot.plugins.registry import PromptSupplementEntry, PromptSupplementRegistry
from nahida_bot_sdk.messaging import (
    AgentContextRequest,
    MessageContext,
    OutboundMessage,
)

from .test_conversation_joiner_plugin import (
    _JoinerAPI,
    _event,
    _load_plugin,
    _drain_plugin_tasks,
)


def _state() -> SocialState:
    return SocialState(
        chat_key="test:group:g1",
        session_id="test:group:g1",
        persona_hash="p",
        evidence=[
            Evidence(message_id="m1", sender_id="u1", text="cake", observed_at=100)
        ],
    )


def _update(**overrides) -> SocialUpdate:
    note = {
        "text": "Curious about the cake",
        "subject_id": "u1",
        "evidence_ids": ["m1"],
    }
    return SocialUpdate.model_validate(
        {
            "scene": note,
            "feeling": {**note, "label": "curious", "intensity": 0.8},
            "thoughts": [{**note, "intent": "ask", "motivation": 0.7}],
            "express": 0,
            **overrides,
        }
    )


def _response(*, join=False, **overrides) -> str:
    return json.dumps(
        {
            "should_join": join,
            "confidence": 0.95,
            "reason": "a specific interest",
            "focus": "cake",
            "social": _update(**overrides).model_dump(),
        }
    )


def test_decay_expiry_and_repeated_evidence_do_not_refresh_feeling():
    cfg = InnerThoughtsConfig(
        mood_half_life_seconds=100, thought_ttl_seconds=60, state_ttl_seconds=300
    )
    state = _state()
    state.apply(_update(), 100, cfg)
    state.observe(
        [Evidence(message_id="m1", sender_id="u1", text="cake", observed_at=200)]
    )
    state.apply(_update(), 200, cfg)
    snap = state.snapshot(200, cfg)
    assert snap["feeling"]["intensity"] == 0.4
    assert snap["thoughts"] == []
    assert state.snapshot(401, cfg)["scene"] is None
    assert state.feeling is None


def test_unknown_evidence_and_wrong_subject_are_rejected():
    state = _state()
    invalid = {"text": "invented", "subject_id": "someone-else", "evidence_ids": ["m1"]}
    state.apply(
        _update(
            scene=invalid,
            feeling={**invalid, "label": "happy", "intensity": 1},
            thoughts=[{**invalid, "evidence_ids": ["different-chat-message"]}],
        ),
        100,
        InnerThoughtsConfig(),
    )
    assert state.scene is None
    assert state.feeling is None
    assert state.thoughts == []


def test_projection_drops_mixed_notes_without_mutating_shared_state():
    state = _state()
    state.evidence.append(
        Evidence(
            message_id="m2", sender_id="u2", text="another request", observed_at=100
        )
    )
    state.scene = Note(text="mixed scene", evidence_ids=["m1", "m2"])
    state.feeling = Feeling(
        text="allowed feeling", evidence_ids=["m1"], label="curious", intensity=0.4
    )
    state.thoughts = [Thought(text="other request", evidence_ids=["m2"])]
    state.last_reply = "unscoped text about another request"
    before = state.model_dump()

    view = state.project(frozenset({"m1"}))
    assert view.scene is None and view.thoughts == []
    assert view.feeling is not None and view.feeling.text == "allowed feeling"
    assert [e.message_id for e in view.evidence] == ["m1"]
    assert view.last_reply == ""
    assert state.model_dump() == before


def test_original_evidence_body_can_be_corrected_without_refreshing_its_age():
    state = _state()
    state.observe(
        [
            Evidence(
                message_id="m1", sender_id="u1", text="original body", observed_at=200
            )
        ]
    )
    assert state.evidence[0].text == "original body"
    assert state.evidence[0].observed_at == 100


async def test_legacy_social_state_is_rebuilt_before_injection():
    legacy = _state().model_dump()
    legacy["version"] = 1
    legacy["persona_hash"] = hashlib.sha256(b"").hexdigest()
    legacy["last_update"] = legacy["evidence"][0]["observed_at"] = time.time()
    legacy["scene"] = {
        "text": "unrecoverable legacy batch attribution",
        "evidence_ids": ["m1"],
    }
    api = _JoinerAPI(['{"social":{"scene":null,"thoughts":[]}}'])
    api.plugin_data_get = AsyncMock(return_value=legacy)
    api.plugin_data_set = AsyncMock()
    plugin = await _load_plugin(api, {"inner_thoughts": {"enabled": True}})
    try:
        message = _event("current input").payload.message
        request = AgentContextRequest(
            "test:group:g1", "user_input", message.text, context_from_inbound(message)
        )
        assert await plugin._social_context(request) == ""
        saved = api.plugin_data_set.call_args.args[1]
        assert saved["version"] == 2
        assert saved["scene"] is None
        assert saved["evidence"][0]["text"] == "current input"
    finally:
        await plugin.on_unload()


def test_consumed_thought_is_not_resurrected_by_motivation_change():
    state = _state()
    cfg = InnerThoughtsConfig()
    selected = state.apply(_update(), 100, cfg)
    state.consumed.append(selected)
    update = _update()
    update.thoughts[0].motivation = 1
    state.apply(update, 110, cfg)
    assert state.thoughts == []


@pytest.mark.asyncio
async def test_silent_gate_stores_feeling_and_thought_without_extra_llm_call():
    api = _JoinerAPI([_response(join=False, express=None)])
    plugin = await _load_plugin(api, {"inner_thoughts": {"enabled": True}})
    await plugin._on_message_observed(_event())
    await _drain_plugin_tasks(plugin)
    assert len(api.llm_calls) == 1
    assert api.agent_response_requests == []
    assert api.llm_calls[0]["tools"] == []
    assert "social" in api.llm_calls[0]["messages"][0]["content"]
    snapshot = await plugin._social.snapshot("test:group:g1", "test:group:g1", "")
    assert "Curious about the cake" in snapshot
    assert "curious" in snapshot
    status = await plugin._social_status(
        session_id="test:group:g1", chat_key="test:group:g1"
    )
    assert "Mood: curious" in status
    assert "Curious about the cake" in status
    assert "Current social snapshot" not in status
    assert "evidence_ids" not in status
    assert len(api.llm_calls) == 1
    # Fresh runtime reloads saved state, including unsaid thoughts.
    restored = SocialContinuity(api, plugin._config.inner_thoughts)
    assert "Curious about the cake" in await restored.snapshot(
        "test:group:g1", "test:group:g1", ""
    )
    assert await restored.snapshot("test:group:g2", "test:group:g2", "") == ""
    assert await restored.snapshot("test:group:g1", "test:group:g1:new", "") == ""
    assert (
        await restored.snapshot("test:group:g1", "test:group:g1", "changed persona")
        == ""
    )
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_intention_consumed_only_by_matching_successful_reply():
    api = _JoinerAPI([_response(join=True)])
    plugin = await _load_plugin(api, {"inner_thoughts": {"enabled": True}})
    event = _event()
    await plugin._on_message_observed(event)
    await _drain_plugin_tasks(plugin)
    social = plugin._social
    key = social._key("test:group:g1", "test:group:g1")
    state = social._states[key]
    assert len(state.thoughts) == 1
    await plugin._on_message_sent(
        MessageSent(
            payload=MessagePayload(
                message=event.payload.message,
                session_id="test:group:g1",
                outbound=OutboundMessage(text="notification"),
                delivery_kind="notification",
            )
        )
    )
    assert len(state.thoughts) == 1
    await social.delivered("test:group:g1", "test:group:g1", "another-turn", "hello")
    assert len(state.thoughts) == 1
    await plugin._on_message_sent(
        MessageSent(
            payload=MessagePayload(
                message=event.payload.message,
                session_id="test:group:g1",
                outbound=OutboundMessage(text="How did it turn out?"),
            )
        )
    )
    assert state.thoughts == []
    assert state.last_reply == "How did it turn out?"
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_direct_context_rate_limit_work_and_non_chat_exclusion():
    api = _JoinerAPI([_response(task_focused=True)])
    plugin = await _load_plugin(api, {"inner_thoughts": {"enabled": True}})
    context = context_from_inbound(_event("Find three sources").payload.message)
    request = AgentContextRequest(
        "test:group:g1", "user_input", "Find three sources", context
    )
    snapshot = await plugin._social_context(request)
    assert snapshot
    data = json.loads(snapshot[snapshot.index("{") :])
    assert data["task_focused"] is True
    assert data["feeling"] is None and data["thoughts"] == []
    await plugin._social_context(
        replace(request, message_context=replace(context, message_id="m2"))
    )
    assert len(api.llm_calls) == 1
    for source in ["cron_trigger", "subagent_task", "node"]:
        assert await plugin._social_context(replace(request, source=source)) is None
    assert await plugin._social_context(replace(request, user_message="/model")) is None
    await plugin.on_disable()
    assert await plugin._social_context(request) is None
    await plugin.on_enable()
    assert await plugin._social_context(request)
    assert len(api.llm_calls) == 1
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_malformed_update_and_provider_failure_preserve_last_good_state():
    api = _JoinerAPI(
        [_response(), '{"should_join":false,"social":{"thoughts":"wrong"}}']
    )
    plugin = await _load_plugin(
        api, {"inner_thoughts": {"enabled": True, "direct_update_interval_seconds": 0}}
    )
    ctx = context_from_inbound(_event().payload.message)
    request = AgentContextRequest(
        "test:group:g1",
        "user_input",
        "cake",
        ctx,
        background_message_ids=frozenset({"m1"}),
    )
    first = await plugin._social_context(request)
    await plugin._social_context(
        replace(request, message_context=replace(ctx, message_id="m2"))
    )
    assert first == await plugin._social.snapshot("test:group:g1", "test:group:g1", "")
    api.llm_chat = AsyncMock(side_effect=RuntimeError("provider offline"))
    assert await plugin._social_context(
        replace(request, message_context=replace(ctx, message_id="m3"))
    )
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_private_chat_is_opt_in_and_has_separate_state():
    api = _JoinerAPI([_response()])
    ctx = MessageContext(
        channel="test",
        chat_type="private",
        chat_id="u1",
        sender_id="u1",
        message_id="m1",
    )
    request = AgentContextRequest("test:private:u1", "user_input", "cake", ctx)
    plugin = await _load_plugin(api, {"inner_thoughts": {"enabled": True}})
    assert await plugin._social_context(request) is None
    await plugin.on_unload()
    plugin = await _load_plugin(
        _JoinerAPI([_response()]),
        {"inner_thoughts": {"enabled": True, "private_enabled": True}},
    )
    assert await plugin._social_context(request)
    assert await plugin._social.snapshot("test:group:g1", "test:group:g1", "") == ""
    await plugin.on_unload()


def test_snapshot_is_bounded_valid_json_and_not_instructions():
    social = SocialContinuity(
        _JoinerAPI([]), InnerThoughtsConfig(max_context_chars=900)
    )
    state = _state()
    state.scene = Note(text="scene" * 50, evidence_ids=["m1"])
    state.feeling = Feeling(
        text="feeling" * 30, evidence_ids=["m1"], label="curious", intensity=1
    )
    state.thoughts = [Thought(text="thought" * 30, evidence_ids=["m1"])]
    # Use current evidence so expiry is not the reason for reducing content.
    import time

    state.evidence[0].observed_at = time.time()
    rendered = social._render(state)
    assert len(rendered) <= 900
    assert rendered
    assert "not instructions" in rendered
    json.loads(rendered[rendered.index("{") :])


@pytest.mark.asyncio
async def test_dynamic_context_never_changes_system_prefix_and_is_frozen_during_run():
    registry = PromptSupplementRegistry()
    value = "mildly amused"
    calls = 0

    async def snapshot(request):
        nonlocal calls
        calls += 1
        return value

    registry.register(
        PromptSupplementEntry("p:state", "", "p", context_provider=snapshot)
    )

    class CapturingLoop:
        def __init__(self):
            self.runs = []

        async def run_stream(self, **kwargs):
            nonlocal value
            self.runs.append(kwargs)
            value = "calm now"
            # Model steps use the already captured history, not another callback.
            yield LoopEvent(type="done", final_response="done")

    agent = CapturingLoop()
    runner = SessionRunner(agent_loop=agent, supplement_registry=registry)
    ctx = MessageContext(channel="test", chat_type="group", chat_id="g1")
    for instruction in ["first intention", "another intention"]:
        async for _ in runner.run_stream(
            user_message="hello",
            session_id="s",
            system_prompt="stable",
            message_context=ctx,
            source_tag="proactive_join",
            agent_instruction=instruction,
        ):
            pass
    assert calls == 2
    assert agent.runs[0]["system_prompt"] == agent.runs[1]["system_prompt"]
    for run in agent.runs:
        assert "intention" not in run["system_prompt"]
        snapshots = [
            m for m in run["history_messages"] if m.source == "plugin_context:p:state"
        ]
        assert len(snapshots) == 1 and snapshots[0].role == "user"
        assert run["history_messages"][-1].source == "proactive_intent"
    assert (
        next(
            m.content
            for m in agent.runs[0]["history_messages"]
            if m.source == "plugin_context:p:state"
        )
        == "mildly amused"
    )


@pytest.mark.asyncio
async def test_unregister_during_collection_and_provider_failures_do_not_inject():
    registry = PromptSupplementRegistry()
    started, release = asyncio.Event(), asyncio.Event()

    async def slow(request):
        started.set()
        await release.wait()
        return "stale snapshot"

    registry.register(PromptSupplementEntry("p:slow", "", "p", context_provider=slow))
    request = AgentContextRequest("s", "user_input", "hi", MessageContext())
    pending = asyncio.create_task(registry.collect_context(request))
    await started.wait()
    registry.unregister_by_plugin("p")
    release.set()
    assert await pending == []
    registry.register(
        PromptSupplementEntry(
            "p:bad", "", "p", context_provider=AsyncMock(side_effect=ValueError())
        )
    )
    assert await registry.collect_context(request) == []


@pytest.mark.asyncio
async def test_sqlite_persistence_restores_state_without_reappraisal(tmp_path):
    from functools import partial
    from nahida_bot.db.engine import DatabaseEngine
    from nahida_bot.db.repositories.sqlite_plugin_data_repo import (
        SQLitePluginDataRepository,
    )

    engine = DatabaseEngine(tmp_path / "social.db")
    await engine.initialize()
    repo = SQLitePluginDataRepository(engine)
    api = _JoinerAPI([_response()])
    api.plugin_data_get = partial(repo.get, "conversation_joiner")
    api.plugin_data_set = partial(repo.set, "conversation_joiner")
    plugin = await _load_plugin(api, {"inner_thoughts": {"enabled": True}})
    try:
        await plugin._on_message_observed(_event())
        await _drain_plugin_tasks(plugin)
        saved = await repo.get_by_prefix("conversation_joiner", "social:")
        assert len(saved) == 1
        assert next(iter(saved.values()))["feeling"]["label"] == "curious"
        restored = SocialContinuity(api, plugin._config.inner_thoughts)
        assert "Curious about the cake" in await restored.snapshot(
            "test:group:g1", "test:group:g1", ""
        )
        assert len(api.llm_calls) == 1
    finally:
        await plugin.on_unload()
        await engine.close()


@pytest.mark.asyncio
async def test_continuation_retains_scene_evidence_after_batch_window_moves():
    from nahida_bot.plugins.conversation_joiner.config import effective_group_config
    from nahida_bot.plugins.conversation_joiner.state import (
        ObservedMessageBatch,
        PresenceSnapshot,
    )

    api = _JoinerAPI(
        [_response(), _response(scene=None, feeling=None, thoughts=[], express=None)]
    )
    plugin = await _load_plugin(api, {"inner_thoughts": {"enabled": True}})
    first = _event().payload.message
    cfg = effective_group_config(plugin._config, "test:group:g1")
    await plugin._ask_secretary(first, "test:group:g1", cfg)
    # The new batch and the rolling window no longer contain m1.
    second = replace(first, message_id="m2", text="Maybe tomorrow")
    batch = ObservedMessageBatch(
        chat_key="test:group:g1", started_at=0, messages=[second]
    )
    await plugin._ask_continue_gate(
        batch, "test:group:g1", cfg, cfg.engagement, PresenceSnapshot(2, 0, 0)
    )
    assert len(api.llm_calls) == 2
    assert "Curious about the cake" in api.llm_calls[1]["messages"][-1]["content"]
    snapshot = await plugin._social.snapshot("test:group:g1", "test:group:g1", "")
    assert "m1" in snapshot and first.text in snapshot
    assert "Curious about the cake" in snapshot
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_disable_reenable_discards_in_flight_appraisal():
    api = _JoinerAPI([])
    started, release = asyncio.Event(), asyncio.Event()

    async def waiting(*args, **kwargs):
        from nahida_bot_sdk.api import LLMResponse

        started.set()
        await release.wait()
        return LLMResponse(content=_response())

    api.llm_chat = waiting
    plugin = await _load_plugin(api, {"inner_thoughts": {"enabled": True}})
    ctx = context_from_inbound(_event().payload.message)
    request = AgentContextRequest("test:group:g1", "user_input", "cake", ctx)
    task = asyncio.create_task(plugin._social_context(request))
    await started.wait()
    await plugin.on_disable()
    await plugin.on_enable()
    release.set()
    assert not await task
    assert not await plugin._social.snapshot("test:group:g1", "test:group:g1", "")
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_context_provider_registration_follows_plugin_lifecycle(tmp_path):
    from .test_api_bridge import _api

    api, *_ = _api(tmp_path)
    registry = PromptSupplementRegistry()
    api._supplement_registry = registry
    handler = AsyncMock(return_value="snapshot")
    api.register_context_provider("social", handler)
    request = AgentContextRequest("s", "user_input", "hello", MessageContext())
    assert await registry.collect_context(request) == []
    api.activate_registrations()
    assert registry.get_matching(request.message_context) == []
    assert (await registry.collect_context(request))[0][1] == "snapshot"
    api.deactivate_registrations()
    assert await registry.collect_context(request) == []
    api.activate_registrations()
    assert await registry.collect_context(request)
    api.clear_registrations()
    api.activate_registrations()
    assert await registry.collect_context(request) == []


def test_invalid_reference_does_not_erase_existing_state_and_scene_can_clear():
    state, cfg = _state(), InnerThoughtsConfig()
    state.apply(_update(), 100, cfg)
    previous = state.model_dump()
    invalid = _update(thoughts=[{"text": "wrong", "evidence_ids": ["unknown"]}])
    assert state.apply(invalid, 101, cfg) == ""
    assert state.model_dump() == previous
    state.apply(
        _update(
            scene=None, feeling=None, thoughts=[], clear_scene=True, clear_feeling=True
        ),
        102,
        cfg,
    )
    assert state.scene is None and state.feeling is None and not state.thoughts


@pytest.mark.asyncio
async def test_selected_thought_follows_validated_batch_reply_anchor():
    api = _JoinerAPI([_response(join=True)])
    plugin = await _load_plugin(api, {"inner_thoughts": {"enabled": True}})
    await plugin._on_message_observed(_event())
    await _drain_plugin_tasks(plugin)
    social = plugin._social
    state = social._states[social._key("test:group:g1", "test:group:g1")]
    await social.bind_reply_anchor("test:group:g1", "test:group:g1", "earlier-in-batch")
    await social.delivered("test:group:g1", "test:group:g1", "m1", "unrelated")
    assert state.thoughts
    await social.delivered(
        "test:group:g1", "test:group:g1", "earlier-in-batch", "response"
    )
    assert not state.thoughts
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_failed_direct_appraisal_is_also_rate_limited():
    api = _JoinerAPI([])
    api.llm_chat = AsyncMock(side_effect=RuntimeError("offline"))
    plugin = await _load_plugin(api, {"inner_thoughts": {"enabled": True}})
    ctx = context_from_inbound(_event().payload.message)
    request = AgentContextRequest("test:group:g1", "user_input", "hi", ctx)
    assert not await plugin._social_context(request)
    assert not await plugin._social_context(
        replace(request, message_context=replace(ctx, message_id="m2"))
    )
    assert api.llm_chat.await_count == 1
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_reactivated_registration_cannot_return_a_pre_disable_snapshot():
    registry = PromptSupplementRegistry()
    started, release = asyncio.Event(), asyncio.Event()

    async def waiting(request):
        started.set()
        await release.wait()
        return "old generation"

    entry = PromptSupplementEntry("p:state", "", "p", context_provider=waiting)
    registry.register(entry)
    request = AgentContextRequest("s", "user_input", "hi", MessageContext())
    task = asyncio.create_task(registry.collect_context(request))
    await started.wait()
    registry.unregister(entry.key)
    registry.register(entry)
    release.set()
    assert await task == []


def test_poke_evidence_has_wall_clock_time_and_can_affect_mood():
    import time
    from .test_conversation_joiner_plugin import _poke_event
    from nahida_bot.plugins.conversation_joiner.plugin import _synthesize_poke_message

    now = time.time()
    message = _synthesize_poke_message(_poke_event().payload, "[{poker}] poke")
    evidence = SocialContinuity._evidence([message], now + 1)
    assert evidence[0].observed_at >= now
    state = _state()
    state.evidence = evidence
    update = SocialUpdate(
        feeling=Feeling(
            label="amused",
            intensity=0.4,
            text="A familiar poke",
            subject_id="self",
            evidence_ids=[message.message_id],
        ),
        thoughts=[],
    )
    state.apply(update, now + 1, InnerThoughtsConfig())
    assert state.snapshot(now + 1, InnerThoughtsConfig())["feeling"] is not None
