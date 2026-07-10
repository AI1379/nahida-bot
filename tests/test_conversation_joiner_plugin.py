"""Tests for the conversation joiner plugin."""

from __future__ import annotations

import asyncio
import time
from typing import Any, cast

import pytest

from nahida_bot.core.events import (
    MessageObserved,
    MessageReceived,
    MessagePayload,
    MessageSent,
    PokeEvent,
    PokePayload,
)
from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.plugins.base import ChatContext, InboundMessage, SenderContext
from nahida_bot.plugins.conversation_joiner.config import effective_group_config
from nahida_bot.plugins.conversation_joiner.plugin import ConversationJoinerPlugin
from nahida_bot.plugins.conversation_joiner.state import EngagementStateMachine
from nahida_bot.plugins.manifest import (
    Capabilities,
    FilesystemPermission,
    Permissions,
    PluginManifest,
)
from nahida_bot_sdk.api import LLMResponse
from nahida_bot_sdk.testing import RecordingMockBotAPI, load_plugin_for_test


class _JoinerAPI(RecordingMockBotAPI):
    def __init__(
        self,
        responses: list[str],
        *,
        active: bool = False,
        active_sessions: dict[str, str] | None = None,
        active_session_ids: set[str] | None = None,
        workspace: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self.responses = list(responses)
        self.llm_calls: list[dict[str, Any]] = []
        self.active = active
        self.active_sessions = dict(active_sessions or {})
        self.active_session_ids = set(active_session_ids or set())
        self.workspace = dict(workspace or {})
        self.workspace_reads: list[str] = []

    async def llm_chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        self.llm_calls.append(
            {
                "messages": messages,
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "tools": tools,
            }
        )
        content = self.responses.pop(0) if self.responses else "{}"
        return LLMResponse(content=content, model=model)

    def get_session_run_status(self, session_id: str) -> dict[str, Any]:
        active = self.active or session_id in self.active_session_ids
        return {
            "active": active,
            "state": "running" if active else "idle",
            "pending_messages": 0,
        }

    def get_active_session_id(self, address: Any) -> str:
        return self.active_sessions.get(address.chat_key, address.chat_key)

    async def workspace_read(self, path: str) -> str:
        self.workspace_reads.append(path)
        return self.workspace.get(path, "")


def _manifest(config: dict[str, Any] | None = None) -> PluginManifest:
    base_config = {
        "enabled": True,
        "model": "cheap",
        "threshold": 0.75,
        "cooldown_seconds": 0,
        "debounce_seconds": 0,
        "max_triggers_per_hour": 3,
        "prefilter": {
            "sample_rate": 1.0,
            "keyword_sample_rate": 1.0,
        },
    }
    if config:
        base_config.update(config)
    return PluginManifest(
        id="conversation_joiner",
        name="Conversation Joiner",
        version="0.1.0",
        entrypoint="nahida_bot.plugins.conversation_joiner.plugin:ConversationJoinerPlugin",
        permissions=Permissions(
            llm_access=True,
            filesystem=FilesystemPermission(read=["workspace"]),
        ),
        capabilities=Capabilities(
            subscribes_to=["MessageObserved", "MessageReceived", "MessageSent"],
            emits=["AgentResponseRequested"],
        ),
        config=base_config,
    )


def _event(text: str = "should the bot join this topic?") -> MessageObserved:
    inbound = InboundMessage(
        message_id="m1",
        platform="test",
        chat_id="g1",
        user_id="u1",
        text=text,
        raw_event={},
        is_group=True,
        chat_context=ChatContext(platform="test", chat_type="group"),
        sender_context=SenderContext(display_name="Alice", platform_user_id="u1"),
    )
    return MessageObserved(
        payload=MessagePayload(message=inbound, session_id="test:group:g1"),
        source="test",
    )


async def _load_plugin(
    api: _JoinerAPI,
    config: dict[str, Any] | None = None,
) -> ConversationJoinerPlugin:
    plugin = ConversationJoinerPlugin(api=api, manifest=_manifest(config))
    await load_plugin_for_test(plugin)
    return plugin


@pytest.mark.asyncio
async def test_should_join_true_requests_agent_response() -> None:
    api = _JoinerAPI(
        [
            '{"should_join": true, "confidence": 0.9, "reason": "helpful", '
            '"entry_style": "short_comment", "focus": "deployment"}'
        ]
    )
    plugin = await _load_plugin(api)
    handler = api.registered_event_handlers[MessageObserved][0]

    await handler(_event())
    await _drain_plugin_tasks(plugin)

    assert len(api.llm_calls) == 1
    assert len(api.agent_response_requests) == 1
    request = api.agent_response_requests[0]
    assert request["session_id"] == "test:group:g1"
    assert request["reason"] == "helpful"
    assert "deployment" in request["instruction"]
    frame = request["attention_frame"]
    assert frame is not None
    assert frame.trigger_kind == "proactive_join"
    assert frame.episode_id
    assert frame.anchor_message_id == "m1"
    assert [message.message_id for message in frame.messages] == ["m1"]
    assert frame.focus == "deployment"


@pytest.mark.asyncio
async def test_should_join_false_does_not_request_agent() -> None:
    api = _JoinerAPI(
        ['{"should_join": false, "confidence": 0.2, "reason": "not useful"}']
    )
    plugin = await _load_plugin(api)

    await api.registered_event_handlers[MessageObserved][0](_event())
    await _drain_plugin_tasks(plugin)

    assert len(api.llm_calls) == 1
    assert api.agent_response_requests == []


@pytest.mark.asyncio
async def test_confidence_below_threshold_does_not_request_agent() -> None:
    api = _JoinerAPI(['{"should_join": true, "confidence": 0.3, "reason": "maybe"}'])
    plugin = await _load_plugin(api, {"threshold": 0.8})

    await api.registered_event_handlers[MessageObserved][0](_event())
    await _drain_plugin_tasks(plugin)

    assert len(api.llm_calls) == 1
    assert api.agent_response_requests == []


@pytest.mark.asyncio
async def test_cooldown_blocks_second_trigger() -> None:
    api = _JoinerAPI(
        [
            '{"should_join": true, "confidence": 0.9, "reason": "first"}',
            '{"should_join": true, "confidence": 0.9, "reason": "second"}',
        ]
    )
    plugin = await _load_plugin(api, {"cooldown_seconds": 300})
    handler = api.registered_event_handlers[MessageObserved][0]

    await handler(_event("first useful topic"))
    await _drain_plugin_tasks(plugin)
    await handler(_event("second useful topic"))
    await _drain_plugin_tasks(plugin)

    assert len(api.agent_response_requests) == 1
    assert len(api.llm_calls) == 1


@pytest.mark.asyncio
async def test_active_run_skips_secretary_call() -> None:
    api = _JoinerAPI(
        ['{"should_join": true, "confidence": 0.9, "reason": "active"}'],
        active=True,
    )
    plugin = await _load_plugin(api)

    await api.registered_event_handlers[MessageObserved][0](_event())
    await _drain_plugin_tasks(plugin)

    assert api.llm_calls == []
    assert api.agent_response_requests == []


@pytest.mark.asyncio
async def test_sample_rate_zero_skips_secretary_call() -> None:
    api = _JoinerAPI(['{"should_join": true, "confidence": 0.9, "reason": "sample"}'])
    plugin = await _load_plugin(
        api,
        {
            "prefilter": {
                "sample_rate": 0.0,
                "keyword_sample_rate": 1.0,
            }
        },
    )

    await api.registered_event_handlers[MessageObserved][0](_event("normal topic"))
    await _drain_plugin_tasks(plugin)

    assert api.llm_calls == []
    assert api.agent_response_requests == []


@pytest.mark.asyncio
async def test_keyword_sample_rate_can_bypass_base_sample_rate() -> None:
    api = _JoinerAPI(
        [
            '{"should_join": true, "confidence": 0.9, "reason": "keyword", '
            '"focus": "keyword topic"}'
        ]
    )
    plugin = await _load_plugin(
        api,
        {
            "prefilter": {
                "sample_rate": 0.0,
                "keyword_sample_rate": 1.0,
                "keyword_hints": ["纳西妲"],
            }
        },
    )

    await api.registered_event_handlers[MessageObserved][0](_event("纳西妲"))
    await _drain_plugin_tasks(plugin)

    assert len(api.llm_calls) == 1
    assert len(api.agent_response_requests) == 1
    assert api.agent_response_requests[0]["reason"] == "keyword"


@pytest.mark.asyncio
async def test_runtime_sample_roll_controls_middle_sample_rate() -> None:
    api = _JoinerAPI(['{"should_join": false, "confidence": 0.1, "reason": "sampled"}'])
    plugin = await _load_plugin(
        api,
        {
            "persona_context": {"enabled": False},
            "prefilter": {
                "sample_rate": 0.5,
                "keyword_sample_rate": 1.0,
            },
        },
    )
    handler = api.registered_event_handlers[MessageObserved][0]

    cast(Any, plugin)._sample_random = lambda: 0.9
    await handler(_event("ordinary topic one"))
    await _drain_plugin_tasks(plugin)

    cast(Any, plugin)._sample_random = lambda: 0.1
    await handler(_event("ordinary topic two"))
    await _drain_plugin_tasks(plugin)

    assert len(api.llm_calls) == 1
    assert api.agent_response_requests == []


@pytest.mark.asyncio
async def test_persona_context_is_injected_and_cached() -> None:
    api = _JoinerAPI(
        [
            '{"should_join": false, "confidence": 0.1, "reason": "first"}',
            '{"should_join": false, "confidence": 0.1, "reason": "second"}',
        ],
        workspace={"SOUL.md": "Main agent is concise and joins technical chats."},
    )
    plugin = await _load_plugin(api)
    handler = api.registered_event_handlers[MessageObserved][0]

    await handler(_event("first technical topic"))
    await _drain_plugin_tasks(plugin)
    await handler(_event("second technical topic"))
    await _drain_plugin_tasks(plugin)

    assert api.workspace_reads == ["SOUL.md"]
    assert len(api.llm_calls) == 2
    prompt = api.llm_calls[0]["messages"][1]["content"]
    assert "Bot persona context" in prompt
    assert "### SOUL.md" in prompt
    assert "Main agent is concise" in prompt


async def _drain_plugin_tasks(plugin: ConversationJoinerPlugin) -> None:
    tasks = [task for task in list(cast(Any, plugin)._tasks) if not task.done()]
    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, asyncio.CancelledError):
                continue
            if isinstance(result, BaseException):
                raise result


# ---------------------------------------------------------------------------
# Engagement helpers
# ---------------------------------------------------------------------------


def _engagement_manifest(
    engagement_overrides: dict[str, Any] | None = None,
    *,
    extra_config: dict[str, Any] | None = None,
) -> PluginManifest:
    """Build a manifest with engagement enabled."""
    engagement: dict[str, Any] = {"enabled": True}
    if engagement_overrides:
        engagement.update(engagement_overrides)
    config: dict[str, Any] = {
        "enabled": True,
        "model": "cheap",
        "threshold": 0.75,
        "cooldown_seconds": 0,
        "debounce_seconds": 0,
        "max_triggers_per_hour": 3,
        "prefilter": {
            "sample_rate": 1.0,
            "keyword_sample_rate": 1.0,
        },
        "persona_context": {"enabled": False},
        "engagement": engagement,
    }
    if extra_config:
        config.update(extra_config)
    return PluginManifest(
        id="conversation_joiner",
        name="Conversation Joiner",
        version="0.1.0",
        entrypoint="nahida_bot.plugins.conversation_joiner.plugin:ConversationJoinerPlugin",
        permissions=Permissions(
            llm_access=True,
            filesystem=FilesystemPermission(read=["workspace"]),
        ),
        capabilities=Capabilities(
            subscribes_to=["MessageObserved", "MessageReceived", "MessageSent"],
            emits=["AgentResponseRequested"],
        ),
        config=config,
    )


def _event_for_group(
    text: str = "should the bot join this topic?",
    *,
    session_id: str = "test:group:g1",
    chat_id: str = "g1",
    user_id: str = "u1",
    is_self: bool = False,
    mentions_bot: bool = False,
    message_id: str | None = None,
) -> MessageObserved:
    inbound = InboundMessage(
        message_id=message_id or f"m-{id(text)}-{text[:4]}",
        platform="test",
        chat_id=chat_id,
        user_id=user_id,
        text=text,
        raw_event={},
        is_group=True,
        mentions_bot=mentions_bot,
        chat_context=ChatContext(platform="test", chat_type="group"),
        sender_context=SenderContext(
            display_name="Alice" if not is_self else "Bot",
            platform_user_id=user_id,
            is_self=is_self,
        ),
    )
    return MessageObserved(
        payload=MessagePayload(message=inbound, session_id=session_id),
        source="test",
    )


def _sent_event(session_id: str) -> MessageSent:
    """Create a MessageSent event for a given session."""
    inbound = InboundMessage(
        message_id="out-1",
        platform="test",
        chat_id="g1",
        user_id="bot",
        text="Bot reply",
        raw_event={},
        is_group=True,
        chat_context=ChatContext(platform="test", chat_type="group"),
        sender_context=SenderContext(
            display_name="Bot",
            platform_user_id="bot",
            is_self=True,
        ),
    )
    return MessageSent(
        payload=MessagePayload(message=inbound, session_id=session_id),
        source="message_router",
    )


def _direct_mention_sent_event(
    session_id: str = "test:group:g1",
) -> MessageSent:
    """MessageSent carrying the original mention-triggering inbound message."""
    inbound = InboundMessage(
        message_id="mention-1",
        platform="test",
        chat_id="g1",
        user_id="u1",
        text="@bot what do you think?",
        raw_event={},
        is_group=True,
        mentions_bot=True,
        chat_context=ChatContext(platform="test", chat_type="group"),
        sender_context=SenderContext(
            display_name="Alice",
            platform_user_id="u1",
        ),
    )
    return MessageSent(
        payload=MessagePayload(message=inbound, session_id=session_id),
        source="message_router",
    )


def _direct_mention_received_event() -> MessageReceived:
    sent = _direct_mention_sent_event()
    return MessageReceived(payload=sent.payload, source="test")


async def _load_engaged_plugin(
    api: _JoinerAPI,
    engagement_overrides: dict[str, Any] | None = None,
    *,
    extra_config: dict[str, Any] | None = None,
) -> ConversationJoinerPlugin:
    plugin = ConversationJoinerPlugin(
        api=api,
        manifest=_engagement_manifest(engagement_overrides, extra_config=extra_config),
    )
    await load_plugin_for_test(plugin)
    return plugin


# ---------------------------------------------------------------------------
# State machine unit tests
# ---------------------------------------------------------------------------


class TestEngagementStateMachine:
    """Unit tests for EngagementStateMachine (state.py)."""

    def _make_sm(self) -> EngagementStateMachine:
        import logging

        logger = logging.getLogger("test")
        return EngagementStateMachine(logger)

    def test_default_state_is_observing(self) -> None:
        sm = self._make_sm()
        state = sm.get_state("g1")
        assert state.state == "observing"
        assert state.engagement_score == 0.5

    def test_transition_to_joining(self) -> None:
        sm = self._make_sm()
        now = time.monotonic()
        sm.transition_to_joining("g1", now)
        state = sm.get_state("g1")
        assert state.state == "joining"
        assert state.episode_id
        assert state.topic_started_at == now

    def test_transition_to_engaged(self) -> None:
        sm = self._make_sm()
        now = time.monotonic()
        sm.transition_to_joining("g1", now)
        joining_episode = sm.get_state("g1").episode_id
        sm.transition_to_engaged("g1", now + 1)
        state = sm.get_state("g1")
        assert state.state == "engaged"
        assert state.episode_id == joining_episode
        assert state.last_agent_reply_at == now + 1
        assert state.low_value_strikes == 0
        # Batch should be created.
        assert sm.get_batch("g1") is not None

    def test_transition_to_observing(self) -> None:
        sm = self._make_sm()
        now = time.monotonic()
        sm.transition_to_joining("g1", now)
        sm.transition_to_engaged("g1", now + 1)
        sm.transition_to_observing("g1", now + 2, reason="test")
        state = sm.get_state("g1")
        assert state.state == "observing"
        assert state.episode_id == ""
        assert sm.get_batch("g1") is None

    def test_direct_engagement_starts_and_reuses_episode(self) -> None:
        sm = self._make_sm()
        now = time.monotonic()

        sm.transition_to_engaged("g1", now)
        first_episode = sm.get_state("g1").episode_id
        sm.transition_to_cooling("g1", now + 1)
        sm.transition_to_engaged("g1", now + 2)

        assert first_episode
        assert sm.get_state("g1").episode_id == first_episode

    def test_transition_to_cooling(self) -> None:
        sm = self._make_sm()
        now = time.monotonic()
        sm.transition_to_joining("g1", now)
        sm.transition_to_engaged("g1", now + 1)
        sm.transition_to_cooling("g1", now + 2)
        state = sm.get_state("g1")
        assert state.state == "cooling"
        assert state.last_triggered_at == now + 2

    def test_cooling_back_to_engaged(self) -> None:
        sm = self._make_sm()
        now = time.monotonic()
        sm.transition_to_joining("g1", now)
        sm.transition_to_engaged("g1", now + 1)
        sm.transition_to_cooling("g1", now + 2)
        # Not yet elapsed.
        assert not sm.try_transition_from_cooling("g1", now + 2.5, cooldown_seconds=10)
        # Elapsed.
        assert sm.try_transition_from_cooling("g1", now + 13, cooldown_seconds=10)
        assert sm.get_state("g1").state == "engaged"

    def test_ewma_score(self) -> None:
        sm = self._make_sm()
        state = sm.get_state("g1")
        assert state.engagement_score == 0.5
        sm.update_engagement_score("g1", 1.0, alpha=0.2)
        # 0.5 * 0.8 + 1.0 * 0.2 = 0.6
        assert abs(state.engagement_score - 0.6) < 1e-9
        sm.update_engagement_score("g1", 0.0, alpha=0.2)
        # 0.6 * 0.8 + 0.0 * 0.2 = 0.48
        assert abs(state.engagement_score - 0.48) < 1e-9

    def test_score_decay_half_life(self) -> None:
        from nahida_bot.plugins.conversation_joiner.config import EngagementConfig

        sm = self._make_sm()
        now = time.monotonic()
        sm.transition_to_joining("g1", now)
        sm.transition_to_engaged("g1", now + 1)
        state = sm.get_state("g1")
        state.engagement_score = 0.8
        state.score_updated_at = now + 1

        cfg = EngagementConfig(
            score_decay_half_life_seconds=10,
            score_decay_floor=0.0,
        )
        sm.decay_engagement_score("g1", now + 11, cfg)

        assert abs(state.engagement_score - 0.4) < 1e-9
        assert state.score_updated_at == now + 11

    def test_score_decay_floor(self) -> None:
        from nahida_bot.plugins.conversation_joiner.config import EngagementConfig

        sm = self._make_sm()
        now = time.monotonic()
        sm.transition_to_joining("g1", now)
        sm.transition_to_engaged("g1", now + 1)
        state = sm.get_state("g1")
        state.engagement_score = 0.8
        state.score_updated_at = now + 1

        cfg = EngagementConfig(
            score_decay_half_life_seconds=10,
            score_decay_floor=0.2,
        )
        sm.decay_engagement_score("g1", now + 11, cfg)

        assert abs(state.engagement_score - 0.5) < 1e-9

    def test_observing_state_does_not_decay_score(self) -> None:
        from nahida_bot.plugins.conversation_joiner.config import EngagementConfig

        sm = self._make_sm()
        state = sm.get_state("g1")
        state.engagement_score = 0.8
        state.score_updated_at = time.monotonic() - 100

        sm.decay_engagement_score(
            "g1",
            time.monotonic(),
            EngagementConfig(score_decay_half_life_seconds=10),
        )

        assert state.engagement_score == 0.8

    def test_low_value_strikes(self) -> None:
        sm = self._make_sm()
        sm.increment_low_value_strike("g1")
        sm.increment_low_value_strike("g1")
        assert sm.get_state("g1").low_value_strikes == 2
        sm.reset_low_value_strikes("g1")
        assert sm.get_state("g1").low_value_strikes == 0

    def test_exit_on_idle_timeout(self) -> None:
        from nahida_bot.plugins.conversation_joiner.config import EngagementConfig

        sm = self._make_sm()
        now = time.monotonic()
        sm.transition_to_joining("g1", now)
        sm.transition_to_engaged("g1", now + 1)
        state = sm.get_state("g1")
        state.last_observed_at = now + 1

        cfg = EngagementConfig(idle_exit_seconds=10.0)
        # Not yet idle.
        assert sm.check_exit_conditions("g1", now + 5, cfg) is None
        # Idle.
        assert sm.check_exit_conditions("g1", now + 12, cfg) == "idle_timeout"

    def test_exit_on_max_engaged_seconds(self) -> None:
        from nahida_bot.plugins.conversation_joiner.config import EngagementConfig

        sm = self._make_sm()
        now = time.monotonic()
        sm.transition_to_joining("g1", now)
        sm.transition_to_engaged("g1", now + 1)
        state = sm.get_state("g1")
        state.last_observed_at = now + 100

        cfg = EngagementConfig(
            max_engaged_seconds=60.0,
            idle_exit_seconds=9999.0,
            join_state_ttl_seconds=9999.0,
        )
        assert sm.check_exit_conditions("g1", now + 62, cfg) == "max_engaged_seconds"

    def test_exit_on_low_value_strikes(self) -> None:
        from nahida_bot.plugins.conversation_joiner.config import EngagementConfig

        sm = self._make_sm()
        now = time.monotonic()
        sm.transition_to_joining("g1", now)
        sm.transition_to_engaged("g1", now + 1)
        state = sm.get_state("g1")
        state.engagement_score = 0.1  # below exit threshold
        state.last_observed_at = now + 100
        for _ in range(3):
            sm.increment_low_value_strike("g1")

        cfg = EngagementConfig(
            engagement_score_exit_threshold=0.2,
            idle_exit_seconds=9999.0,
            join_state_ttl_seconds=9999.0,
            max_engaged_seconds=9999.0,
            exit_gate={"enabled": True, "low_value_strikes": 3},
        )
        assert sm.check_exit_conditions("g1", now + 10, cfg) == "low_value_strikes"

    def test_exit_gate_disabled_disables_low_value_exit(self) -> None:
        from nahida_bot.plugins.conversation_joiner.config import EngagementConfig

        sm = self._make_sm()
        now = time.monotonic()
        sm.transition_to_joining("g1", now)
        sm.transition_to_engaged("g1", now + 1)
        state = sm.get_state("g1")
        state.engagement_score = 0.1
        state.last_observed_at = now + 100
        for _ in range(3):
            sm.increment_low_value_strike("g1")

        cfg = EngagementConfig(
            engagement_score_exit_threshold=0.2,
            idle_exit_seconds=9999.0,
            join_state_ttl_seconds=9999.0,
            max_engaged_seconds=9999.0,
            exit_gate={"enabled": False, "low_value_strikes": 3},
        )
        assert sm.check_exit_conditions("g1", now + 10, cfg) is None

    def test_no_exit_for_observing_state(self) -> None:
        from nahida_bot.plugins.conversation_joiner.config import EngagementConfig

        sm = self._make_sm()
        cfg = EngagementConfig()
        assert sm.check_exit_conditions("g1", time.monotonic(), cfg) is None

    def test_append_to_batch(self) -> None:
        from nahida_bot.plugins.conversation_joiner.config import EngagementConfig

        sm = self._make_sm()
        now = time.monotonic()
        sm.transition_to_joining("g1", now)
        sm.transition_to_engaged("g1", now + 1)

        msg = InboundMessage(
            message_id="m1",
            platform="test",
            chat_id="g1",
            user_id="u1",
            text="hello world",
            raw_event={},
            is_group=True,
            chat_context=ChatContext(platform="test", chat_type="group"),
        )
        cfg = EngagementConfig(batching={"max_messages": 2, "max_chars": 9999})
        full = sm.append_to_batch("g1", msg, cfg, now + 2)
        assert not full
        assert len(sm.get_batch("g1").messages) == 1

        full = sm.append_to_batch("g1", msg, cfg, now + 3)
        assert full
        assert len(sm.get_batch("g1").messages) == 2

    def test_append_to_batch_keeps_sliding_message_cap(self) -> None:
        from nahida_bot.plugins.conversation_joiner.config import EngagementConfig

        sm = self._make_sm()
        now = time.monotonic()
        sm.transition_to_joining("g1", now)
        sm.transition_to_engaged("g1", now + 1)
        cfg = EngagementConfig(batching={"max_messages": 2, "max_chars": 9999})

        for i in range(4):
            sm.append_to_batch(
                "g1",
                InboundMessage(
                    message_id=f"m{i}",
                    platform="test",
                    chat_id="g1",
                    user_id="u1",
                    text=f"message {i}",
                    raw_event={},
                    is_group=True,
                    chat_context=ChatContext(platform="test", chat_type="group"),
                ),
                cfg,
                now + 2 + i,
            )

        batch = sm.get_batch("g1")
        assert batch is not None
        assert [msg.message_id for msg in batch.messages] == ["m2", "m3"]

    def test_clear_batch(self) -> None:
        from nahida_bot.plugins.conversation_joiner.config import EngagementConfig

        sm = self._make_sm()
        now = time.monotonic()
        sm.transition_to_joining("g1", now)
        sm.transition_to_engaged("g1", now + 1)

        msg = InboundMessage(
            message_id="m1",
            platform="test",
            chat_id="g1",
            user_id="u1",
            text="hello",
            raw_event={},
            is_group=True,
            chat_context=ChatContext(platform="test", chat_type="group"),
        )
        cfg = EngagementConfig()
        sm.append_to_batch("g1", msg, cfg, now + 2)
        assert len(sm.get_batch("g1").messages) == 1

        sm.clear_batch("g1")
        # Re-created empty batch.
        batch = sm.get_batch("g1")
        assert batch is not None
        assert len(batch.messages) == 0

    def test_serialize_deserialize_roundtrip(self) -> None:
        sm = self._make_sm()
        now = time.monotonic()
        sm.transition_to_joining("g1", now)
        sm.transition_to_engaged("g1", now + 1)
        sm.increment_low_value_strike("g1")
        sm.update_engagement_score("g1", 0.9, alpha=0.2)

        data = sm.serialize_state("g1")
        assert data is not None
        assert data["state"] == "engaged"
        assert data["episode_id"]

        sm2 = self._make_sm()
        sm2.deserialize_state("g1", data)
        restored = sm2.get_state("g1")
        assert restored.state == "engaged"
        assert restored.episode_id == data["episode_id"]
        assert (
            abs(restored.engagement_score - sm.get_state("g1").engagement_score) < 1e-9
        )
        assert restored.low_value_strikes == 1


# ---------------------------------------------------------------------------
# Engagement integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engagement_disabled_unchanged_behavior() -> None:
    """When engagement is disabled (default), existing flow is identical."""
    api = _JoinerAPI(
        [
            '{"should_join": true, "confidence": 0.9, "reason": "test"}',
            '{"should_join": true, "confidence": 0.9, "reason": "second"}',
        ],
    )
    plugin = await _load_plugin(api)
    # Engagement disabled by default — no state machine.
    assert cast(Any, plugin)._sm is None

    handler = api.registered_event_handlers[MessageObserved][0]
    await handler(_event())
    await _drain_plugin_tasks(plugin)

    assert len(api.agent_response_requests) == 1


@pytest.mark.asyncio
async def test_engagement_enabled_creates_state_machine() -> None:
    """When engagement.enabled=true, the state machine is initialized."""
    api = _JoinerAPI([])
    plugin = await _load_engaged_plugin(api)
    assert cast(Any, plugin)._sm is not None
    assert cast(Any, plugin)._sm.get_state("test:group:g1").state == "observing"


@pytest.mark.asyncio
async def test_group_engagement_override_can_enable_state_machine() -> None:
    api = _JoinerAPI(
        ['{"should_join": true, "confidence": 0.9, "reason": "topic"}'],
    )
    plugin = ConversationJoinerPlugin(
        api=api,
        manifest=_manifest(
            {
                "engagement": {"enabled": False},
                "groups": {
                    "test:group:g1": {
                        "engagement": {"enabled": True},
                    },
                },
            }
        ),
    )
    await load_plugin_for_test(plugin)

    assert cast(Any, plugin)._sm is not None
    await api.registered_event_handlers[MessageObserved][0](
        _event_for_group("topic starter")
    )
    await _drain_plugin_tasks(plugin)

    assert cast(Any, plugin)._sm.get_state("test:group:g1").state == "joining"


@pytest.mark.asyncio
async def test_group_engagement_override_can_disable_state_machine_path() -> None:
    api = _JoinerAPI(
        ['{"should_join": true, "confidence": 0.9, "reason": "topic"}'],
    )
    plugin = await _load_engaged_plugin(
        api,
        extra_config={
            "groups": {
                "test:group:g1": {
                    "engagement": {"enabled": False},
                },
            },
        },
    )

    await api.registered_event_handlers[MessageObserved][0](
        _event_for_group("topic starter")
    )
    await _drain_plugin_tasks(plugin)

    assert len(api.agent_response_requests) == 1
    assert cast(Any, plugin)._sm.get_batch("test:group:g1") is None


@pytest.mark.asyncio
async def test_observing_to_joining_transition() -> None:
    """Join gate passes → state transitions to joining."""
    api = _JoinerAPI(
        ['{"should_join": true, "confidence": 0.9, "reason": "relevant topic"}'],
    )
    plugin = await _load_engaged_plugin(api)
    handler = api.registered_event_handlers[MessageObserved][0]

    await handler(_event_for_group("let us discuss AI"))
    await _drain_plugin_tasks(plugin)

    sm = cast(Any, plugin)._sm
    state = sm.get_state("test:group:g1")
    assert state.state == "joining"
    assert len(api.agent_response_requests) == 1


@pytest.mark.asyncio
async def test_joining_to_engaged_on_message_sent() -> None:
    """MessageSent event for the session → state transitions to engaged."""
    api = _JoinerAPI(
        ['{"should_join": true, "confidence": 0.9, "reason": "good topic"}'],
    )
    plugin = await _load_engaged_plugin(api)

    # Trigger the join.
    handler = api.registered_event_handlers[MessageObserved][0]
    await handler(_event_for_group("good topic"))
    await _drain_plugin_tasks(plugin)

    sm = cast(Any, plugin)._sm
    assert sm.get_state("test:group:g1").state == "joining"

    # Simulate MessageSent from the router.
    sent_handler = api.registered_event_handlers[MessageSent][0]
    await sent_handler(_sent_event("test:group:g1"))

    state = sm.get_state("test:group:g1")
    assert state.state == "engaged"
    assert sm.get_batch("test:group:g1") is not None


@pytest.mark.asyncio
async def test_direct_mention_reply_enters_engaged_state() -> None:
    """A visible reply to @bot starts participation without a joiner request."""
    api = _JoinerAPI([])
    plugin = await _load_engaged_plugin(api)
    sm = cast(Any, plugin)._sm
    assert sm.get_state("test:group:g1").state == "observing"

    received_handler = api.registered_event_handlers[MessageReceived][0]
    await received_handler(_direct_mention_received_event())
    await _drain_plugin_tasks(plugin)
    assert sm.get_state("test:group:g1").state == "observing"
    assert api.agent_response_requests == []
    assert api.llm_calls == []

    sent_handler = api.registered_event_handlers[MessageSent][0]
    await sent_handler(_direct_mention_sent_event())

    state = sm.get_state("test:group:g1")
    assert state.state == "engaged"
    assert state.topic_started_at > 0
    assert state.last_agent_reply_at > 0
    assert sm.get_batch("test:group:g1") is not None
    contexts = cast(Any, plugin)._contexts["test:group:g1"]
    assert contexts[-1].message_id == "mention-1"
    assert len(contexts) == 1

    # Streaming may emit multiple MessageSent events for one inbound trigger;
    # the same mention must not reset engagement repeatedly.
    topic_started_at = state.topic_started_at
    await sent_handler(_direct_mention_sent_event())
    assert sm.get_state("test:group:g1").topic_started_at == topic_started_at


@pytest.mark.asyncio
async def test_joining_to_observing_on_no_reply() -> None:
    """Agent run completes without MessageSent → back to observing."""
    api = _JoinerAPI(
        ['{"should_join": true, "confidence": 0.9, "reason": "good topic"}'],
    )
    plugin = await _load_engaged_plugin(
        api,
        extra_config={"decision_timeout_seconds": 0.1},
    )

    handler = api.registered_event_handlers[MessageObserved][0]
    await handler(_event_for_group("good topic"))

    # First drain: runs the observed handler which spawns the monitor task.
    await _drain_plugin_tasks(plugin)
    # Second drain: runs the monitor task (sleeps decision_timeout*3 = 0.3s).
    await _drain_plugin_tasks(plugin)

    sm = cast(Any, plugin)._sm
    state = sm.get_state("test:group:g1")
    assert state.state == "observing"
    # Score should have dropped.
    assert state.engagement_score < 0.5


@pytest.mark.asyncio
async def test_engaged_state_appends_to_batch() -> None:
    """In engaged state, observed messages are appended to the batch."""
    # Provide a continue_gate response in case the batch fills up.
    api = _JoinerAPI(
        [
            '{"should_join": true, "confidence": 0.9, "reason": "topic"}',
            '{"should_join": false, "confidence": 0.1, "reason": "not yet"}',
        ],
    )
    plugin = await _load_engaged_plugin(
        api,
        engagement_overrides={
            "batching": {"max_messages": 20, "max_chars": 10000, "window_seconds": 60},
        },
    )

    # Trigger join.
    handler = api.registered_event_handlers[MessageObserved][0]
    await handler(_event_for_group("topic starter"))
    await _drain_plugin_tasks(plugin)

    # Confirm engagement.
    sm = cast(Any, plugin)._sm
    sent_handler = api.registered_event_handlers[MessageSent][0]
    await sent_handler(_sent_event("test:group:g1"))
    assert sm.get_state("test:group:g1").state == "engaged"

    # Send more messages while engaged — they go to batch.
    await handler(_event_for_group("follow up one", message_id="m2"))
    await handler(_event_for_group("follow up two", message_id="m3"))
    await _drain_plugin_tasks(plugin)

    batch = sm.get_batch("test:group:g1")
    assert batch is not None
    assert len(batch.messages) == 2


@pytest.mark.asyncio
async def test_message_sent_without_pending_is_ignored() -> None:
    """Uncorrelated MessageSent events do not move engaged state to cooling."""
    api = _JoinerAPI(
        ['{"should_join": true, "confidence": 0.9, "reason": "topic"}'],
    )
    plugin = await _load_engaged_plugin(api)

    handler = api.registered_event_handlers[MessageObserved][0]
    await handler(_event_for_group("topic starter"))
    await _drain_plugin_tasks(plugin)

    sm = cast(Any, plugin)._sm
    sent_handler = api.registered_event_handlers[MessageSent][0]
    await sent_handler(_sent_event("test:group:g1"))
    assert sm.get_state("test:group:g1").state == "engaged"

    await sent_handler(_sent_event("test:group:g1"))
    state = sm.get_state("test:group:g1")
    assert state.state == "engaged"


@pytest.mark.asyncio
async def test_message_sent_wrong_session_does_not_confirm_pending() -> None:
    active_session = "test:group:g1:abc12345"
    api = _JoinerAPI(
        ['{"should_join": true, "confidence": 0.9, "reason": "topic"}'],
        active_sessions={"test:group:g1": active_session},
    )
    plugin = await _load_engaged_plugin(api)

    handler = api.registered_event_handlers[MessageObserved][0]
    sent_handler = api.registered_event_handlers[MessageSent][0]
    await handler(_event_for_group("topic starter", session_id="test:group:g1"))
    await _drain_plugin_tasks(plugin)

    sm = cast(Any, plugin)._sm
    assert sm.get_state("test:group:g1").state == "joining"
    assert "test:group:g1" in cast(Any, plugin)._pending_requests

    await sent_handler(_sent_event("test:group:g1:different"))
    await asyncio.sleep(0)
    assert sm.get_state("test:group:g1").state == "joining"
    assert "test:group:g1" in cast(Any, plugin)._pending_requests

    await sent_handler(_sent_event(active_session))
    await asyncio.sleep(0)
    assert sm.get_state("test:group:g1").state == "engaged"
    assert cast(Any, plugin)._pending_requests == {}


@pytest.mark.asyncio
async def test_message_sent_accepts_current_active_session_after_switch() -> None:
    old_session = "test:group:g1:old12345"
    new_session = "test:group:g1:new12345"
    api = _JoinerAPI(
        ['{"should_join": true, "confidence": 0.9, "reason": "topic"}'],
        active_sessions={"test:group:g1": old_session},
    )
    plugin = await _load_engaged_plugin(api)

    handler = api.registered_event_handlers[MessageObserved][0]
    sent_handler = api.registered_event_handlers[MessageSent][0]
    await handler(_event_for_group("topic starter", session_id="test:group:g1"))
    await _drain_plugin_tasks(plugin)
    assert api.agent_response_requests[0]["session_id"] == old_session

    api.active_sessions["test:group:g1"] = new_session
    await sent_handler(_sent_event(new_session))
    await asyncio.sleep(0)

    sm = cast(Any, plugin)._sm
    assert sm.get_state("test:group:g1").state == "engaged"
    assert cast(Any, plugin)._pending_requests == {}


@pytest.mark.asyncio
async def test_cooling_to_engaged_after_cooldown() -> None:
    """After response_cooldown_seconds elapses, cooling transitions back to engaged."""
    api = _JoinerAPI(
        ['{"should_join": true, "confidence": 0.9, "reason": "topic"}'],
    )
    plugin = await _load_engaged_plugin(
        api,
        engagement_overrides={
            "response_cooldown_seconds": 5.0,
            "batching": {"max_messages": 2},
        },
    )

    handler = api.registered_event_handlers[MessageObserved][0]
    await handler(_event_for_group("topic starter"))
    await _drain_plugin_tasks(plugin)

    sm = cast(Any, plugin)._sm
    sent_handler = api.registered_event_handlers[MessageSent][0]
    await sent_handler(_sent_event("test:group:g1"))

    # Manually transition to cooling with an old timestamp to simulate elapsed cooldown.
    now = time.monotonic()
    sm.transition_to_cooling("test:group:g1", now - 10)  # 10s ago, cooldown is 5s
    assert sm.get_state("test:group:g1").state == "cooling"

    # Send another message — should detect cooldown elapsed and transition to engaged.
    await handler(_event_for_group("new message after cooldown"))
    await _drain_plugin_tasks(plugin)

    state = sm.get_state("test:group:g1")
    assert state.state == "engaged"


@pytest.mark.asyncio
async def test_exit_on_idle_timeout_integration() -> None:
    """Idle timeout exits engagement back to observing."""
    api = _JoinerAPI(
        ['{"should_join": true, "confidence": 0.9, "reason": "topic"}'],
    )
    plugin = await _load_engaged_plugin(
        api,
        engagement_overrides={
            "idle_exit_seconds": 180.0,
            "max_engaged_seconds": 9999,
            "join_state_ttl_seconds": 9999,
        },
    )

    handler = api.registered_event_handlers[MessageObserved][0]
    await handler(_event_for_group("topic"))
    await _drain_plugin_tasks(plugin)

    sm = cast(Any, plugin)._sm
    sent_handler = api.registered_event_handlers[MessageSent][0]
    await sent_handler(_sent_event("test:group:g1"))
    assert sm.get_state("test:group:g1").state == "engaged"

    # Manually set last_observed_at far into the past to simulate idle.
    state = sm.get_state("test:group:g1")
    state.last_observed_at = time.monotonic() - 200  # idle_exit is 180s

    # Send another message — should detect idle and exit to observing.
    await handler(_event_for_group("late message"))
    await _drain_plugin_tasks(plugin)

    state = sm.get_state("test:group:g1")
    assert state.state == "observing"


@pytest.mark.asyncio
async def test_engagement_score_updates_on_feedback() -> None:
    """Verify that positive/negative feedback updates the EWMA score."""
    api = _JoinerAPI(
        ['{"should_join": true, "confidence": 0.9, "reason": "topic"}'],
    )
    plugin = await _load_engaged_plugin(
        api,
        extra_config={"decision_timeout_seconds": 0.1},
    )

    handler = api.registered_event_handlers[MessageObserved][0]
    await handler(_event_for_group("topic"))
    await _drain_plugin_tasks(plugin)

    sm = cast(Any, plugin)._sm
    initial_score = sm.get_state("test:group:g1").engagement_score

    # No reply (monitor detects) → score drops.
    await _drain_plugin_tasks(plugin)
    score_after_no_reply = sm.get_state("test:group:g1").engagement_score
    assert score_after_no_reply < initial_score


@pytest.mark.asyncio
async def test_observed_message_applies_score_decay() -> None:
    api = _JoinerAPI(
        ['{"should_join": true, "confidence": 0.9, "reason": "topic"}'],
    )
    plugin = await _load_engaged_plugin(
        api,
        engagement_overrides={
            "score_decay_half_life_seconds": 10,
            "score_decay_floor": 0.0,
            "batching": {"max_messages": 20, "max_chars": 10000},
        },
    )
    handler = api.registered_event_handlers[MessageObserved][0]
    sent_handler = api.registered_event_handlers[MessageSent][0]

    await handler(_event_for_group("topic starter"))
    await _drain_plugin_tasks(plugin)
    await sent_handler(_sent_event("test:group:g1"))

    sm = cast(Any, plugin)._sm
    state = sm.get_state("test:group:g1")
    state.engagement_score = 0.8
    state.score_updated_at = time.monotonic() - 10
    state.last_observed_at = time.monotonic()

    await handler(_event_for_group("follow-up", message_id="m2"))
    await _drain_plugin_tasks(plugin)

    assert 0.35 < state.engagement_score < 0.41
    sm.cancel_all_timers()


@pytest.mark.asyncio
async def test_status_provider_applies_score_decay() -> None:
    api = _JoinerAPI(
        ['{"should_join": true, "confidence": 0.9, "reason": "topic"}'],
    )
    plugin = await _load_engaged_plugin(
        api,
        engagement_overrides={
            "score_decay_half_life_seconds": 10,
            "score_decay_floor": 0.0,
        },
    )
    handler = api.registered_event_handlers[MessageObserved][0]
    sent_handler = api.registered_event_handlers[MessageSent][0]

    await handler(_event_for_group("topic starter"))
    await _drain_plugin_tasks(plugin)
    await sent_handler(_sent_event("test:group:g1"))

    sm = cast(Any, plugin)._sm
    state = sm.get_state("test:group:g1")
    state.engagement_score = 0.8
    state.score_updated_at = time.monotonic() - 10

    status = await cast(Any, plugin)._status_provider(
        session_id="test:group:g1",
        chat_key="test:group:g1",
    )

    assert status is not None
    assert "Score:" in status
    assert 0.35 < state.engagement_score < 0.41


# ---------------------------------------------------------------------------
# Batch flush & continue gate tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_full_triggers_continue_gate_and_agent_request() -> None:
    """When batch is full, continue_gate is called and agent is requested."""
    # First response: join gate. Second: continue gate (pass).
    api = _JoinerAPI(
        [
            '{"should_join": true, "confidence": 0.9, "reason": "topic"}',
            (
                '{"should_join": true, "confidence": 0.7, '
                '"reason": "relevant follow-up", "reply_mode": "direct_reply", '
                '"reply_anchor_message_id": "m2"}'
            ),
        ],
    )
    plugin = await _load_engaged_plugin(
        api,
        engagement_overrides={
            "response_cooldown_seconds": 5.0,
            "batching": {"max_messages": 2},
        },
    )

    handler = api.registered_event_handlers[MessageObserved][0]
    # Step 1: Join.
    await handler(_event_for_group("topic starter"))
    await _drain_plugin_tasks(plugin)

    sm = cast(Any, plugin)._sm
    sent_handler = api.registered_event_handlers[MessageSent][0]
    await sent_handler(_sent_event("test:group:g1"))
    assert sm.get_state("test:group:g1").state == "engaged"

    # Step 2: Fill batch to max_messages=2.
    await handler(_event_for_group("follow up one", message_id="m2"))
    await handler(_event_for_group("follow up two", message_id="m3"))
    await _drain_plugin_tasks(plugin)

    # continue_gate should have been called and agent requested.
    assert len(api.agent_response_requests) == 2
    second_req = api.agent_response_requests[1]
    assert "batch of 2" in second_req["reason"]
    assert second_req["message"].message_id == "m2"
    assert [msg.message_id for msg in second_req["observed_messages"]] == ["m2", "m3"]
    assert second_req["reply_to_message_id"] == "m2"
    first_frame = api.agent_response_requests[0]["attention_frame"]
    second_frame = second_req["attention_frame"]
    assert first_frame.episode_id
    assert second_frame.episode_id == first_frame.episode_id
    continue_prompt = api.llm_calls[1]["messages"][1]["content"]
    assert "[m2] Alice: follow up one" in continue_prompt
    assert "[m3] Alice: follow up two" in continue_prompt
    state = sm.get_state("test:group:g1")
    assert state.state == "engaged"
    assert "test:group:g1" in cast(Any, plugin)._pending_requests

    await sent_handler(_sent_event("test:group:g1"))
    state = sm.get_state("test:group:g1")
    assert state.state == "cooling"


@pytest.mark.asyncio
async def test_continue_gate_false_keeps_silent() -> None:
    """When continue_gate returns false, no agent request is made."""
    api = _JoinerAPI(
        [
            '{"should_join": true, "confidence": 0.9, "reason": "topic"}',
            '{"should_join": false, "confidence": 0.2, "reason": "not relevant"}',
        ],
    )
    plugin = await _load_engaged_plugin(
        api,
        engagement_overrides={
            "response_cooldown_seconds": 5.0,
            "batching": {"max_messages": 2},
        },
    )

    handler = api.registered_event_handlers[MessageObserved][0]
    await handler(_event_for_group("topic starter"))
    await _drain_plugin_tasks(plugin)

    sm = cast(Any, plugin)._sm
    sent_handler = api.registered_event_handlers[MessageSent][0]
    await sent_handler(_sent_event("test:group:g1"))
    assert sm.get_state("test:group:g1").state == "engaged"

    # Fill batch.
    await handler(_event_for_group("off-topic one", message_id="m2"))
    await handler(_event_for_group("off-topic two", message_id="m3"))
    await _drain_plugin_tasks(plugin)

    # Only the initial join request, no batch flush request.
    assert len(api.agent_response_requests) == 1
    # State should still be engaged (not cooling).
    state = sm.get_state("test:group:g1")
    assert state.state == "engaged"
    # Strikes should have incremented.
    assert state.low_value_strikes >= 1


@pytest.mark.asyncio
async def test_continue_gate_group_comment_uses_no_reply_anchor() -> None:
    api = _JoinerAPI(
        [
            '{"should_join": true, "confidence": 0.9, "reason": "topic"}',
            (
                '{"should_join": true, "confidence": 0.8, '
                '"reason": "ambient comment", "reply_mode": "group_comment"}'
            ),
        ],
    )
    plugin = await _load_engaged_plugin(
        api,
        engagement_overrides={
            "response_cooldown_seconds": 5.0,
            "batching": {"max_messages": 2},
        },
    )

    handler = api.registered_event_handlers[MessageObserved][0]
    sent_handler = api.registered_event_handlers[MessageSent][0]
    await handler(_event_for_group("topic starter"))
    await _drain_plugin_tasks(plugin)
    await sent_handler(_sent_event("test:group:g1"))

    await handler(_event_for_group("follow up one", message_id="m2"))
    await handler(_event_for_group("follow up two", message_id="m3"))
    await _drain_plugin_tasks(plugin)

    second_req = api.agent_response_requests[1]
    assert second_req["message"].message_id == "m3"
    assert [msg.message_id for msg in second_req["observed_messages"]] == ["m2", "m3"]
    assert second_req["reply_to_message_id"] == ""


@pytest.mark.asyncio
async def test_continue_gate_no_reply_mode_keeps_silent() -> None:
    api = _JoinerAPI(
        [
            '{"should_join": true, "confidence": 0.9, "reason": "topic"}',
            (
                '{"should_join": true, "confidence": 0.8, '
                '"reason": "do not speak", "reply_mode": "no_reply"}'
            ),
        ],
    )
    plugin = await _load_engaged_plugin(
        api,
        engagement_overrides={
            "response_cooldown_seconds": 5.0,
            "batching": {"max_messages": 2},
        },
    )
    handler = api.registered_event_handlers[MessageObserved][0]
    sent_handler = api.registered_event_handlers[MessageSent][0]

    await handler(_event_for_group("topic starter"))
    await _drain_plugin_tasks(plugin)
    await sent_handler(_sent_event("test:group:g1"))

    await handler(_event_for_group("follow up one", message_id="m2"))
    await handler(_event_for_group("follow up two", message_id="m3"))
    await _drain_plugin_tasks(plugin)

    assert len(api.agent_response_requests) == 1
    sm = cast(Any, plugin)._sm
    batch = sm.get_batch("test:group:g1")
    assert batch is not None
    assert batch.messages == []


@pytest.mark.asyncio
async def test_flush_on_mention_immediately_flushes() -> None:
    """When flush_on_mention is true, a mention triggers immediate flush."""
    api = _JoinerAPI(
        [
            '{"should_join": true, "confidence": 0.9, "reason": "topic"}',
            '{"should_join": true, "confidence": 0.8, "reason": "bot mentioned"}',
        ],
    )
    plugin = await _load_engaged_plugin(
        api,
        engagement_overrides={
            "response_cooldown_seconds": 5.0,
            "batching": {
                "flush_on_mention": True,
                "max_messages": 20,
                "max_chars": 10000,
            },
        },
    )

    handler = api.registered_event_handlers[MessageObserved][0]
    await handler(_event_for_group("topic starter"))
    await _drain_plugin_tasks(plugin)

    sm = cast(Any, plugin)._sm
    sent_handler = api.registered_event_handlers[MessageSent][0]
    await sent_handler(_sent_event("test:group:g1"))
    assert sm.get_state("test:group:g1").state == "engaged"

    # Send one regular message, then a mention.
    await handler(_event_for_group("some message", message_id="m2"))
    await handler(
        _event_for_group(
            "@bot please help", message_id="m3", is_self=False, mentions_bot=True
        )
    )
    await _drain_plugin_tasks(plugin)

    assert len(api.agent_response_requests) == 2
    state = sm.get_state("test:group:g1")
    assert state.state == "engaged"

    await sent_handler(_sent_event("test:group:g1"))
    state = sm.get_state("test:group:g1")
    assert state.state == "cooling"


@pytest.mark.asyncio
async def test_engaged_to_cooling_via_batch_flush() -> None:
    """After batch flush, engaged transitions to cooling, then back."""
    api = _JoinerAPI(
        [
            '{"should_join": true, "confidence": 0.9, "reason": "topic"}',
            '{"should_join": true, "confidence": 0.8, "reason": "continue"}',
        ],
    )
    plugin = await _load_engaged_plugin(
        api,
        engagement_overrides={
            "response_cooldown_seconds": 5.0,
            "batching": {"max_messages": 2},
        },
    )

    handler = api.registered_event_handlers[MessageObserved][0]
    await handler(_event_for_group("topic"))
    await _drain_plugin_tasks(plugin)

    sm = cast(Any, plugin)._sm
    sent_handler = api.registered_event_handlers[MessageSent][0]
    await sent_handler(_sent_event("test:group:g1"))
    assert sm.get_state("test:group:g1").state == "engaged"

    # Fill batch → continue_gate passes → cooling.
    await handler(_event_for_group("follow up one", message_id="m2"))
    await handler(_event_for_group("follow up two", message_id="m3"))
    await _drain_plugin_tasks(plugin)

    state = sm.get_state("test:group:g1")
    assert state.state == "engaged"

    await sent_handler(_sent_event("test:group:g1"))
    state = sm.get_state("test:group:g1")
    assert state.state == "cooling"

    # Simulate cooling elapsed, then a new message brings it back to engaged.
    now = time.monotonic()
    sm.transition_to_cooling("test:group:g1", now - 10)  # 10s ago, cooldown is 5s
    assert sm.get_state("test:group:g1").state == "cooling"

    await handler(_event_for_group("new message", message_id="m4"))
    await _drain_plugin_tasks(plugin)
    state = sm.get_state("test:group:g1")
    assert state.state == "engaged"


@pytest.mark.asyncio
async def test_continue_gate_uses_lower_threshold() -> None:
    """continue_gate uses its own threshold (0.55), not join gate (0.75)."""
    api = _JoinerAPI(
        [
            '{"should_join": true, "confidence": 0.9, "reason": "topic"}',
            # confidence 0.6 would fail join_gate (0.75) but passes continue_gate (0.55).
            '{"should_join": true, "confidence": 0.6, "reason": "mildly relevant"}',
        ],
    )
    plugin = await _load_engaged_plugin(
        api,
        engagement_overrides={
            "response_cooldown_seconds": 5.0,
            "batching": {"max_messages": 2},
            "continue_gate": {"enabled": True, "threshold": 0.55},
        },
    )

    handler = api.registered_event_handlers[MessageObserved][0]
    await handler(_event_for_group("topic"))
    await _drain_plugin_tasks(plugin)

    sm = cast(Any, plugin)._sm
    sent_handler = api.registered_event_handlers[MessageSent][0]
    await sent_handler(_sent_event("test:group:g1"))
    assert sm.get_state("test:group:g1").state == "engaged"

    # Fill batch.
    await handler(_event_for_group("msg1", message_id="m2"))
    await handler(_event_for_group("msg2", message_id="m3"))
    await _drain_plugin_tasks(plugin)

    # The 0.6 confidence should pass the 0.55 continue_gate threshold.
    assert len(api.agent_response_requests) == 2
    await sent_handler(_sent_event("test:group:g1"))
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_normal_group_engagement_flow_with_active_session_override() -> None:
    """Observed chat-key events still use the router's active session override."""
    active_session = "test:group:g1:abc12345"
    api = _JoinerAPI(
        [
            '{"should_join": true, "confidence": 0.9, "reason": "topic"}',
            '{"should_join": true, "confidence": 0.8, "reason": "follow-up"}',
        ],
        active_sessions={"test:group:g1": active_session},
    )
    plugin = await _load_engaged_plugin(
        api,
        engagement_overrides={
            "response_cooldown_seconds": 5.0,
            "batching": {"max_messages": 2},
        },
    )

    handler = api.registered_event_handlers[MessageObserved][0]
    sent_handler = api.registered_event_handlers[MessageSent][0]

    await handler(_event_for_group("topic starter", session_id="test:group:g1"))
    await _drain_plugin_tasks(plugin)
    assert api.agent_response_requests[0]["session_id"] == active_session

    sm = cast(Any, plugin)._sm
    await sent_handler(_sent_event(active_session))
    await asyncio.sleep(0)
    assert sm.get_state("test:group:g1").state == "engaged"
    assert cast(Any, plugin)._monitor_tasks == {}

    await handler(_event_for_group("follow one", message_id="m2"))
    await handler(_event_for_group("follow two", message_id="m3"))
    await _drain_plugin_tasks(plugin)
    assert api.agent_response_requests[1]["session_id"] == active_session
    assert sm.get_state("test:group:g1").state == "engaged"

    await sent_handler(_sent_event(active_session))
    await asyncio.sleep(0)
    assert sm.get_state("test:group:g1").state == "cooling"
    assert cast(Any, plugin)._monitor_tasks == {}


@pytest.mark.asyncio
async def test_message_sent_feedback_uses_group_engagement_config() -> None:
    api = _JoinerAPI(
        [
            '{"should_join": true, "confidence": 0.9, "reason": "topic"}',
            '{"should_join": true, "confidence": 0.8, "reason": "follow-up"}',
        ],
    )
    plugin = await _load_engaged_plugin(
        api,
        extra_config={
            "groups": {
                "test:group:g1": {
                    "engagement": {
                        "enabled": True,
                        "engagement_score_alpha": 1.0,
                        "batching": {"max_messages": 2},
                    },
                },
            },
        },
    )
    handler = api.registered_event_handlers[MessageObserved][0]
    sent_handler = api.registered_event_handlers[MessageSent][0]

    await handler(_event_for_group("topic starter"))
    await _drain_plugin_tasks(plugin)
    await sent_handler(_sent_event("test:group:g1"))

    sm = cast(Any, plugin)._sm
    await handler(_event_for_group("follow one", message_id="m2"))
    await handler(_event_for_group("follow two", message_id="m3"))
    await _drain_plugin_tasks(plugin)

    state = sm.get_state("test:group:g1")
    state.engagement_score = 0.2
    await sent_handler(_sent_event("test:group:g1"))

    assert state.engagement_score == 0.8


@pytest.mark.asyncio
async def test_active_session_override_active_run_skips_join_gate() -> None:
    active_session = "test:group:g1:abc12345"
    api = _JoinerAPI(
        ['{"should_join": true, "confidence": 0.9, "reason": "topic"}'],
        active_sessions={"test:group:g1": active_session},
        active_session_ids={active_session},
    )
    plugin = await _load_engaged_plugin(api)

    await api.registered_event_handlers[MessageObserved][0](
        _event_for_group("topic while active", session_id="test:group:g1")
    )
    await _drain_plugin_tasks(plugin)

    assert api.llm_calls == []
    assert api.agent_response_requests == []
    assert cast(Any, plugin)._sm.get_state("test:group:g1").state == "observing"


@pytest.mark.asyncio
async def test_message_sent_cancels_pending_monitor() -> None:
    api = _JoinerAPI(
        ['{"should_join": true, "confidence": 0.9, "reason": "topic"}'],
    )
    plugin = await _load_engaged_plugin(api)
    handler = api.registered_event_handlers[MessageObserved][0]
    sent_handler = api.registered_event_handlers[MessageSent][0]

    await handler(_event_for_group("topic starter"))
    await _drain_plugin_tasks(plugin)
    assert "test:group:g1" in cast(Any, plugin)._monitor_tasks

    await sent_handler(_sent_event("test:group:g1"))
    await asyncio.sleep(0)

    assert cast(Any, plugin)._pending_requests == {}
    assert cast(Any, plugin)._monitor_tasks == {}


@pytest.mark.asyncio
async def test_continue_gate_min_messages_keeps_buffering() -> None:
    api = _JoinerAPI(
        ['{"should_join": true, "confidence": 0.9, "reason": "topic"}'],
    )
    plugin = await _load_engaged_plugin(
        api,
        engagement_overrides={
            "continue_gate": {"enabled": True, "min_messages": 3},
            "batching": {"max_messages": 20, "max_chars": 10000, "window_seconds": 60},
        },
    )
    handler = api.registered_event_handlers[MessageObserved][0]
    sent_handler = api.registered_event_handlers[MessageSent][0]

    await handler(_event_for_group("topic starter"))
    await _drain_plugin_tasks(plugin)
    await sent_handler(_sent_event("test:group:g1"))
    await asyncio.sleep(0)

    await handler(_event_for_group("only one follow-up", message_id="m2"))
    await _drain_plugin_tasks(plugin)

    sm = cast(Any, plugin)._sm
    cfg = effective_group_config(cast(Any, plugin)._config, "test:group:g1")
    address = ChatAddress(channel="test", target_type="group", target_id="g1")
    await cast(Any, plugin)._flush_batch(
        "test:group:g1", address, cfg, cfg.engagement, "test:group:g1"
    )

    assert len(api.agent_response_requests) == 1
    batch = sm.get_batch("test:group:g1")
    assert batch is not None
    assert len(batch.messages) == 1
    assert sm.has_window_timer("test:group:g1")
    sm.cancel_all_timers()


@pytest.mark.asyncio
async def test_blocked_flush_keeps_batch_bounded() -> None:
    api = _JoinerAPI(
        [
            '{"should_join": true, "confidence": 0.9, "reason": "topic"}',
            '{"should_join": true, "confidence": 0.8, "reason": "follow-up"}',
        ],
    )
    plugin = await _load_engaged_plugin(
        api,
        engagement_overrides={
            "batching": {"max_messages": 2, "max_chars": 10000, "window_seconds": 60},
        },
    )
    handler = api.registered_event_handlers[MessageObserved][0]
    sent_handler = api.registered_event_handlers[MessageSent][0]

    await handler(_event_for_group("topic starter"))
    await _drain_plugin_tasks(plugin)
    await sent_handler(_sent_event("test:group:g1"))

    api.active = True
    await handler(_event_for_group("follow one", message_id="m2"))
    await handler(_event_for_group("follow two", message_id="m3"))
    await handler(_event_for_group("follow three", message_id="m4"))
    await _drain_plugin_tasks(plugin)

    sm = cast(Any, plugin)._sm
    batch = sm.get_batch("test:group:g1")
    assert batch is not None
    assert [msg.message_id for msg in batch.messages] == ["m3", "m4"]
    assert len(api.agent_response_requests) == 1
    sm.cancel_all_timers()


@pytest.mark.asyncio
async def test_engaged_flush_respects_hourly_budget() -> None:
    api = _JoinerAPI(
        [
            '{"should_join": true, "confidence": 0.9, "reason": "topic"}',
            '{"should_join": true, "confidence": 0.8, "reason": "follow-up"}',
        ],
    )
    plugin = await _load_engaged_plugin(
        api,
        engagement_overrides={"batching": {"max_messages": 2}},
        extra_config={"max_triggers_per_hour": 1},
    )
    handler = api.registered_event_handlers[MessageObserved][0]
    sent_handler = api.registered_event_handlers[MessageSent][0]

    await handler(_event_for_group("topic starter"))
    await _drain_plugin_tasks(plugin)
    await sent_handler(_sent_event("test:group:g1"))
    await asyncio.sleep(0)

    await handler(_event_for_group("follow one", message_id="m2"))
    await handler(_event_for_group("follow two", message_id="m3"))
    await _drain_plugin_tasks(plugin)

    assert len(api.agent_response_requests) == 1
    sm = cast(Any, plugin)._sm
    batch = sm.get_batch("test:group:g1")
    assert batch is not None
    assert batch.messages == []
    assert sm.get_state("test:group:g1").state == "engaged"


# ---------------------------------------------------------------------------
# Poke (interaction event) integration — see docs/design/conversation-joiner.md §16
# ---------------------------------------------------------------------------


def _poke_prefilter(**overrides: Any) -> dict[str, Any]:
    base = {
        "sample_rate": 0.0,
        "keyword_sample_rate": 1.0,
        "enable_poke": True,
        "poke_sample_rate": 1.0,
        "poke_text_template": "[{poker}] 戳了戳你",
    }
    base.update(overrides)
    return base


def _poke_event(
    *,
    group_id: str = "g1",
    user_id: str = "u-poker",
    channel: str = "milky",
) -> PokeEvent:
    address = ChatAddress(channel=channel, target_type="group", target_id=group_id)
    payload = PokePayload(
        session_id=f"{channel}:group:{group_id}",
        chat_address=address,
        scene="group",
        group_id=group_id,
        user_id=user_id,
        target_user_id="bot-self",
        display_action="戳了戳",
        display_suffix="",
        raw={"display_action_img_url": ""},
    )
    return PokeEvent(payload=payload, source="milky")


def test_synthesize_poke_message_fields() -> None:
    """Synthesized poke message carries the poke marker and never mentions_bot."""
    from nahida_bot.plugins.conversation_joiner.plugin import _synthesize_poke_message

    msg = _synthesize_poke_message(_poke_event().payload, "[{poker}] 戳了戳你")

    assert msg.mentions_bot is False
    assert msg.is_group is True
    assert msg.message_context is not None
    assert "poke" in msg.message_context.extra_tags
    assert msg.text == "[u-poker] 戳了戳你"
    assert msg.raw_event.get("poke") is True
    assert msg.raw_event.get("scene") == "group"
    assert msg.message_id.startswith("poke:u-poker:")
    assert msg.sender_context is not None
    assert msg.sender_context.is_self is False
    assert msg.sender_context.is_bot is False


@pytest.mark.asyncio
async def test_poke_sample_rate_zero_skips_secretary() -> None:
    api = _JoinerAPI(['{"should_join": true, "confidence": 0.9, "reason": "poke"}'])
    plugin = await _load_plugin(
        api, {"prefilter": _poke_prefilter(poke_sample_rate=0.0)}
    )
    handler = api.registered_event_handlers[PokeEvent][0]

    await handler(_poke_event())
    await _drain_plugin_tasks(plugin)

    assert api.llm_calls == []
    assert api.agent_response_requests == []


@pytest.mark.asyncio
async def test_poke_passes_tag_to_secretary_and_requests_agent() -> None:
    api = _JoinerAPI(
        ['{"should_join": true, "confidence": 0.9, "reason": "poke reply"}']
    )
    plugin = await _load_plugin(
        api, {"prefilter": _poke_prefilter(poke_sample_rate=1.0)}
    )
    handler = api.registered_event_handlers[PokeEvent][0]

    await handler(_poke_event())
    await _drain_plugin_tasks(plugin)

    assert len(api.llm_calls) == 1
    prompt = api.llm_calls[0]["messages"][1]["content"]
    assert "<poke>" in prompt
    assert len(api.agent_response_requests) == 1


@pytest.mark.asyncio
async def test_poke_in_engaged_goes_to_batch_without_flush() -> None:
    """A poke in engaged state is batched like any message; mentions_bot=False
    means flush_on_mention does NOT immediately trigger an agent request."""
    api = _JoinerAPI(['{"should_join": false, "confidence": 0.1, "reason": "no"}'])
    plugin = await _load_engaged_plugin(
        api,
        engagement_overrides={
            "batching": {"max_messages": 20, "max_chars": 10000, "window_seconds": 60},
            "response_cooldown_seconds": 45,
        },
        extra_config={"prefilter": _poke_prefilter()},
    )
    sm = cast(Any, plugin)._sm
    import time as _time

    sm.transition_to_engaged("milky:group:g1", _time.monotonic())

    handler = api.registered_event_handlers[PokeEvent][0]
    await handler(_poke_event())
    await _drain_plugin_tasks(plugin)

    batch = sm.get_batch("milky:group:g1")
    assert batch is not None
    assert len(batch.messages) == 1
    assert "poke" in batch.messages[0].message_context.extra_tags
    assert sm.get_state("milky:group:g1").state == "engaged"
    assert api.agent_response_requests == []


@pytest.mark.asyncio
async def test_poke_in_cooling_does_not_break_cooldown() -> None:
    """A poke must not break the response cooldown (it is a weak signal)."""
    api = _JoinerAPI(['{"should_join": false, "confidence": 0.1, "reason": "no"}'])
    plugin = await _load_engaged_plugin(
        api,
        engagement_overrides={"response_cooldown_seconds": 45},
        extra_config={"prefilter": _poke_prefilter()},
    )
    sm = cast(Any, plugin)._sm
    import time as _time

    now = _time.monotonic()
    chat_key = "milky:group:g1"
    sm.transition_to_engaged(chat_key, now)
    sm.transition_to_cooling(chat_key, now)

    handler = api.registered_event_handlers[PokeEvent][0]
    await handler(_poke_event())
    await _drain_plugin_tasks(plugin)

    assert sm.get_state(chat_key).state == "cooling"
    assert api.agent_response_requests == []


@pytest.mark.asyncio
async def test_friend_scene_poke_is_ignored() -> None:
    """Friend-scene pokes are out of scope (state machine is group-only)."""
    api = _JoinerAPI(['{"should_join": true, "confidence": 0.9, "reason": "x"}'])
    plugin = await _load_plugin(api, {"prefilter": _poke_prefilter()})
    handler = api.registered_event_handlers[PokeEvent][0]

    payload = _poke_event().payload
    friend_payload = PokePayload(
        session_id=payload.session_id,
        chat_address=ChatAddress(
            channel="milky", target_type="private", target_id="u-poker"
        ),
        scene="friend",
        group_id="",
        user_id="u-poker",
        target_user_id="bot-self",
        display_action="戳了戳",
        display_suffix="",
        raw={},
    )
    await handler(PokeEvent(payload=friend_payload, source="milky"))
    await _drain_plugin_tasks(plugin)

    assert api.llm_calls == []
    assert api.agent_response_requests == []


@pytest.mark.asyncio
async def test_enable_poke_false_drops_poke_event() -> None:
    """With enable_poke off, the handler short-circuits before any work."""
    api = _JoinerAPI(['{"should_join": true, "confidence": 0.9, "reason": "x"}'])
    plugin = await _load_plugin(api, {"prefilter": _poke_prefilter(enable_poke=False)})

    # Not subscribed, so invoke the handler directly.
    await cast(Any, plugin)._on_poke(_poke_event())
    await _drain_plugin_tasks(plugin)

    assert api.llm_calls == []
    assert api.agent_response_requests == []
