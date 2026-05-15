"""Tests for DeepSeek provider thinking mode and reasoning_content replay."""

from __future__ import annotations

import json
from typing import Any, cast

import httpx
import pytest

from nahida_bot.agent.context import ContextBudget, ContextBuilder, ContextMessage
from nahida_bot.agent.providers.deepseek import DeepSeekProvider
from nahida_bot.agent.tokenization import CharacterEstimateTokenizer
from nahida_bot.core.runtime_settings import (
    ReasoningRuntimeSettings,
    RuntimeSettings,
    current_runtime_settings,
)


def _build_transport(handler):  # noqa: ANN001
    return httpx.MockTransport(handler)


def _mock_client(transport: httpx.MockTransport):  # noqa: ANN001
    class _MockClient(httpx.AsyncClient):
        def __init__(self, *args: Any, **kwargs: Any):
            super().__init__(*args, transport=transport, **kwargs)

    return _MockClient


# ── _extra_payload: thinking mode ──


@pytest.mark.asyncio
async def test_thinking_enabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DeepSeek provider should send thinking parameter when enabled (default)."""
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    monkeypatch.setattr(
        "nahida_bot.agent.providers.openai_compatible.httpx.AsyncClient",
        _mock_client(_build_transport(handler)),
    )

    provider = DeepSeekProvider(
        base_url="https://api.deepseek.com",
        api_key="x",
        model="deepseek-v4-pro",
    )
    await provider.chat(
        messages=[ContextMessage(role="user", source="u", content="hi")]
    )

    assert captured["thinking"] == {"type": "enabled"}
    assert "reasoning_effort" not in captured


@pytest.mark.asyncio
async def test_thinking_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DeepSeek provider should not send thinking when disabled."""
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    monkeypatch.setattr(
        "nahida_bot.agent.providers.openai_compatible.httpx.AsyncClient",
        _mock_client(_build_transport(handler)),
    )

    provider = DeepSeekProvider(
        base_url="https://api.deepseek.com",
        api_key="x",
        model="deepseek-chat",
        thinking_enabled=False,
    )
    await provider.chat(
        messages=[ContextMessage(role="user", source="u", content="hi")]
    )

    assert "thinking" not in captured


@pytest.mark.asyncio
async def test_reasoning_effort_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DeepSeek provider should send reasoning_effort when configured."""
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    monkeypatch.setattr(
        "nahida_bot.agent.providers.openai_compatible.httpx.AsyncClient",
        _mock_client(_build_transport(handler)),
    )

    provider = DeepSeekProvider(
        base_url="https://api.deepseek.com",
        api_key="x",
        model="deepseek-v4-pro",
        reasoning_effort="max",
    )
    await provider.chat(
        messages=[ContextMessage(role="user", source="u", content="hi")]
    )

    assert captured["thinking"] == {"type": "enabled"}
    assert captured["reasoning_effort"] == "max"


@pytest.mark.asyncio
async def test_runtime_reasoning_effort_overrides_configured_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DeepSeek should use per-session runtime effort for the request."""
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    monkeypatch.setattr(
        "nahida_bot.agent.providers.openai_compatible.httpx.AsyncClient",
        _mock_client(_build_transport(handler)),
    )

    provider = DeepSeekProvider(
        base_url="https://api.deepseek.com",
        api_key="x",
        model="deepseek-v4-pro",
        reasoning_effort="high",
    )
    token = current_runtime_settings.set(
        RuntimeSettings(
            reasoning=ReasoningRuntimeSettings(effort="max"),
        )
    )
    try:
        await provider.chat(
            messages=[ContextMessage(role="user", source="u", content="hi")]
        )
    finally:
        current_runtime_settings.reset(token)

    assert captured["reasoning_effort"] == "max"


# ── reasoning_content replay for tool-call turns ──


def test_reasoning_replay_with_tool_calls_and_reasoning() -> None:
    """reasoning_content should be present when tool_calls and reasoning exist."""
    provider = DeepSeekProvider(
        base_url="https://api.deepseek.com",
        api_key="x",
        model="deepseek-v4-pro",
    )
    msg = ContextMessage(
        role="assistant",
        source="provider_response",
        content="",
        reasoning="I need to call the weather API.",
        metadata={
            "tool_calls": [
                {
                    "id": "call_1",
                    "name": "get_weather",
                    "arguments": {"city": "Hangzhou"},
                }
            ]
        },
    )
    serialized = provider._serialize_message(msg)

    assert serialized["reasoning_content"] == "I need to call the weather API."
    assert "tool_calls" in serialized


def test_reasoning_replay_with_tool_calls_empty_reasoning() -> None:
    """reasoning_content must be included even when empty for tool-call turns."""
    provider = DeepSeekProvider(
        base_url="https://api.deepseek.com",
        api_key="x",
        model="deepseek-v4-pro",
    )
    msg = ContextMessage(
        role="assistant",
        source="provider_response",
        content="",
        reasoning=None,
        metadata={
            "tool_calls": [
                {
                    "id": "call_1",
                    "name": "get_weather",
                    "arguments": {"city": "Hangzhou"},
                }
            ]
        },
    )
    serialized = provider._serialize_message(msg)

    assert serialized["reasoning_content"] == ""
    assert "tool_calls" in serialized


def test_no_reasoning_injection_without_tool_calls() -> None:
    """reasoning_content should NOT be injected for non-tool-call turns without reasoning."""
    provider = DeepSeekProvider(
        base_url="https://api.deepseek.com",
        api_key="x",
        model="deepseek-v4-pro",
    )
    msg = ContextMessage(
        role="assistant",
        source="provider_response",
        content="The answer is 42.",
        reasoning=None,
    )
    serialized = provider._serialize_message(msg)

    assert "reasoning_content" not in serialized


def test_reasoning_present_without_tool_calls() -> None:
    """Non-tool-call assistant messages with reasoning should still include it."""
    provider = DeepSeekProvider(
        base_url="https://api.deepseek.com",
        api_key="x",
        model="deepseek-v4-pro",
    )
    msg = ContextMessage(
        role="assistant",
        source="provider_response",
        content="The answer.",
        reasoning="Step by step...",
    )
    serialized = provider._serialize_message(msg)

    assert serialized["reasoning_content"] == "Step by step..."


def test_full_tool_call_roundtrip_serialization() -> None:
    """Verify complete multi-turn tool-call message serialization."""
    provider = DeepSeekProvider(
        base_url="https://api.deepseek.com",
        api_key="x",
        model="deepseek-v4-pro",
    )

    messages = [
        ContextMessage(role="user", source="u", content="What's the weather?"),
        # Turn 1.1: assistant reasons + calls get_date
        ContextMessage(
            role="assistant",
            source="provider_response",
            content="Let me check the date first.",
            reasoning="Need today's date to find tomorrow's weather.",
            metadata={
                "tool_calls": [{"id": "call_date", "name": "get_date", "arguments": {}}]
            },
        ),
        # Tool result for get_date
        ContextMessage(
            role="tool",
            source="tool_result:get_date",
            content='{"result": "2026-05-02"}',
            metadata={"tool_call_id": "call_date", "tool_name": "get_date"},
        ),
        # Turn 1.2: assistant reasons + calls get_weather
        ContextMessage(
            role="assistant",
            source="provider_response",
            content="",
            reasoning="Today is 2026-05-02. Now call weather for Hangzhou tomorrow.",
            metadata={
                "tool_calls": [
                    {
                        "id": "call_weather",
                        "name": "get_weather",
                        "arguments": {"city": "Hangzhou", "date": "2026-05-03"},
                    }
                ]
            },
        ),
        # Tool result for get_weather
        ContextMessage(
            role="tool",
            source="tool_result:get_weather",
            content='{"result": "Cloudy 7-13C"}',
            metadata={"tool_call_id": "call_weather", "tool_name": "get_weather"},
        ),
        # Turn 1.3: final answer (no tool calls)
        ContextMessage(
            role="assistant",
            source="provider_response",
            content="Tomorrow will be cloudy, 7-13C in Hangzhou.",
            reasoning="Got the result, share it with user.",
        ),
    ]

    serialized = provider.serialize_messages(messages)

    # Turn 1.1: reasoning + tool_calls both present
    assert (
        serialized[1]["reasoning_content"]
        == "Need today's date to find tomorrow's weather."
    )
    first_tool_calls = cast(list[dict[str, Any]], serialized[1]["tool_calls"])
    assert first_tool_calls[0]["function"]["name"] == "get_date"

    # Tool result has tool_call_id
    assert serialized[2]["tool_call_id"] == "call_date"

    # Turn 1.2: reasoning + tool_calls both present
    assert (
        serialized[3]["reasoning_content"]
        == "Today is 2026-05-02. Now call weather for Hangzhou tomorrow."
    )
    second_tool_calls = cast(list[dict[str, Any]], serialized[3]["tool_calls"])
    assert second_tool_calls[0]["function"]["name"] == "get_weather"

    # Tool result has tool_call_id
    assert serialized[4]["tool_call_id"] == "call_weather"

    # Turn 1.3: reasoning present, no tool_calls
    assert serialized[5]["reasoning_content"] == "Got the result, share it with user."
    assert "tool_calls" not in serialized[5]


def test_context_budgeted_tool_round_remains_deepseek_protocol_valid() -> None:
    """Protected active tool transcripts should serialize as valid DeepSeek input."""
    builder = ContextBuilder(
        budget=ContextBudget(max_tokens=90, reserved_tokens=0, summary_max_chars=80),
        fallback_tokenizer=CharacterEstimateTokenizer(chars_per_token=10),
    )
    provider = DeepSeekProvider(
        base_url="https://api.deepseek.com",
        api_key="x",
        model="deepseek-v4-pro",
    )

    prompt = builder.build_context(
        system_prompt="baseline",
        history_messages=[
            ContextMessage(role="user", source="history", content="old " * 120)
        ],
        protected_messages=[
            ContextMessage(role="user", source="user_input", content="weather?"),
            ContextMessage(
                role="assistant",
                source="provider_response",
                content="",
                reasoning="Need a tool result before answering.",
                metadata={
                    "tool_calls": [
                        {
                            "id": "call_weather",
                            "name": "get_weather",
                            "arguments": {"city": "Hangzhou"},
                        }
                    ]
                },
            ),
            ContextMessage(
                role="tool",
                source="tool_result:get_weather",
                content="x" * 500,
                metadata={
                    "tool_call_id": "call_weather",
                    "tool_name": "get_weather",
                },
            ),
        ],
    )

    serialized = provider.serialize_messages(prompt)

    assert provider._serialized_protocol_issues(serialized) == []
    assistant = next(
        message for message in serialized if message.get("role") == "assistant"
    )
    tool = next(message for message in serialized if message.get("role") == "tool")
    tool_calls = cast(list[dict[str, Any]], assistant["tool_calls"])
    assert assistant["reasoning_content"] == "Need a tool result before answering."
    assert tool_calls[0]["id"] == "call_weather"
    assert tool["tool_call_id"] == "call_weather"
    assert len(str(tool["content"])) < 500
