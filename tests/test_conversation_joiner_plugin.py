"""Tests for the conversation joiner plugin."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from nahida_bot.core.events import MessageObserved, MessagePayload
from nahida_bot.plugins.base import ChatContext, InboundMessage, SenderContext
from nahida_bot.plugins.conversation_joiner.plugin import ConversationJoinerPlugin
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
        workspace: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self.responses = list(responses)
        self.llm_calls: list[dict[str, Any]] = []
        self.active = active
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
        return {
            "active": self.active,
            "state": "running" if self.active else "idle",
            "pending_messages": 0,
        }

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
            subscribes_to=["MessageObserved"],
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
    tasks = list(cast(Any, plugin)._tasks)
    if tasks:
        await asyncio.gather(*tasks)
