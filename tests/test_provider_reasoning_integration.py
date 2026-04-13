"""Tests for Phase 2.8 provider reasoning integration."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from nahida_bot.agent.context import ContextMessage
from nahida_bot.agent.providers.base import ProviderResponse, TokenUsage
from nahida_bot.agent.providers.openai_compatible import OpenAICompatibleProvider


def _build_transport(handler):  # noqa: ANN001
    return httpx.MockTransport(handler)


# ── DeepSeek reasoning_content extraction ──


@pytest.mark.asyncio
async def test_deepseek_reasoning_content_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider should extract reasoning_content from DeepSeek-R1 responses."""

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "The answer is 42.",
                        "reasoning_content": "<think>\nStep by step...\n</think>",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 50,
                "completion_tokens": 800,
                "total_tokens": 850,
                "completion_tokens_details": {"reasoning_tokens": 600},
            },
        }
        return httpx.Response(200, json=payload)

    transport = _build_transport(handler)

    class _MockClient(httpx.AsyncClient):
        def __init__(self, *args: Any, **kwargs: Any):
            super().__init__(*args, transport=transport, **kwargs)

    monkeypatch.setattr(
        "nahida_bot.agent.providers.openai_compatible.httpx.AsyncClient", _MockClient
    )

    provider = OpenAICompatibleProvider(
        base_url="https://api.deepseek.com",
        api_key="x",
        model="deepseek-reasoner",
    )
    result = await provider.chat(
        messages=[ContextMessage(role="user", source="u", content="hi")]
    )

    assert result.content == "The answer is 42."
    assert result.reasoning_content == "<think>\nStep by step...\n</think>"
    assert result.usage is not None
    assert result.usage.reasoning_tokens == 600


# ── Think tag fallback extraction ──


@pytest.mark.asyncio
async def test_think_tag_fallback_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider should fall back to <think/> tag extraction when no native field."""

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "<think>Let me analyze...</think>The answer.",
                    },
                    "finish_reason": "stop",
                }
            ]
        }
        return httpx.Response(200, json=payload)

    transport = _build_transport(handler)

    class _MockClient(httpx.AsyncClient):
        def __init__(self, *args: Any, **kwargs: Any):
            super().__init__(*args, transport=transport, **kwargs)

    monkeypatch.setattr(
        "nahida_bot.agent.providers.openai_compatible.httpx.AsyncClient", _MockClient
    )

    provider = OpenAICompatibleProvider(
        base_url="https://example.com/v1",
        api_key="x",
        model="test",
    )
    result = await provider.chat(
        messages=[ContextMessage(role="user", source="u", content="hi")]
    )

    assert result.content == "The answer."
    assert result.reasoning_content == "Let me analyze..."


# ── Reasoning injection in serialized messages ──


@pytest.mark.asyncio
async def test_reasoning_injected_in_serialized_assistant_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider should inject reasoning_content into assistant messages for multi-turn."""
    captured_payload: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_payload
        captured_payload = json.loads(request.content.decode("utf-8"))
        payload = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "done"},
                    "finish_reason": "stop",
                }
            ]
        }
        return httpx.Response(200, json=payload)

    transport = _build_transport(handler)

    class _MockClient(httpx.AsyncClient):
        def __init__(self, *args: Any, **kwargs: Any):
            super().__init__(*args, transport=transport, **kwargs)

    monkeypatch.setattr(
        "nahida_bot.agent.providers.openai_compatible.httpx.AsyncClient", _MockClient
    )

    provider = OpenAICompatibleProvider(
        base_url="https://example.com/v1",
        api_key="x",
        model="test",
    )
    await provider.chat(
        messages=[
            ContextMessage(role="user", source="u", content="hi"),
            ContextMessage(
                role="assistant",
                source="provider_response",
                content="The answer is 42.",
                reasoning="Step by step analysis...",
            ),
            ContextMessage(role="user", source="u", content="why?"),
        ]
    )

    assistant_msg = captured_payload["messages"][1]
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["reasoning_content"] == "Step by step analysis..."


# ── Groq strips reasoning from history ──


@pytest.mark.asyncio
async def test_groq_strips_reasoning_from_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GroqProvider should strip reasoning fields from serialized assistant messages."""
    captured_payload: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_payload
        captured_payload = json.loads(request.content.decode("utf-8"))
        payload = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "done"},
                    "finish_reason": "stop",
                }
            ]
        }
        return httpx.Response(200, json=payload)

    transport = _build_transport(handler)

    class _MockClient(httpx.AsyncClient):
        def __init__(self, *args: Any, **kwargs: Any):
            super().__init__(*args, transport=transport, **kwargs)

    monkeypatch.setattr(
        "nahida_bot.agent.providers.openai_compatible.httpx.AsyncClient", _MockClient
    )

    from nahida_bot.agent.providers.groq import GroqProvider

    provider = GroqProvider(
        base_url="https://api.groq.com/openai/v1",
        api_key="x",
        model="test",
    )
    await provider.chat(
        messages=[
            ContextMessage(role="user", source="u", content="hi"),
            ContextMessage(
                role="assistant",
                source="provider_response",
                content="The answer.",
                reasoning="Some reasoning",
            ),
        ]
    )

    assistant_msg = captured_payload["messages"][1]
    assert "reasoning_content" not in assistant_msg
    assert "reasoning" not in assistant_msg


# ── Token usage parsing ──


@pytest.mark.asyncio
async def test_token_usage_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider should parse usage statistics from response."""

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "hello"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "prompt_tokens_details": {"cached_tokens": 20},
                "completion_tokens_details": {"reasoning_tokens": 10},
            },
        }
        return httpx.Response(200, json=payload)

    transport = _build_transport(handler)

    class _MockClient(httpx.AsyncClient):
        def __init__(self, *args: Any, **kwargs: Any):
            super().__init__(*args, transport=transport, **kwargs)

    monkeypatch.setattr(
        "nahida_bot.agent.providers.openai_compatible.httpx.AsyncClient", _MockClient
    )

    provider = OpenAICompatibleProvider(
        base_url="https://example.com/v1",
        api_key="x",
        model="test",
    )
    result = await provider.chat(
        messages=[ContextMessage(role="user", source="u", content="hi")]
    )

    assert result.usage is not None
    assert result.usage.input_tokens == 100
    assert result.usage.output_tokens == 50
    assert result.usage.cached_tokens == 20
    assert result.usage.reasoning_tokens == 10
    assert result.usage.total == 150


# ── Refusal extraction ──


@pytest.mark.asyncio
async def test_refusal_field_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider should extract refusal field from OpenAI responses."""

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "refusal": "I cannot help with that.",
                    },
                    "finish_reason": "content_filter",
                }
            ]
        }
        return httpx.Response(200, json=payload)

    transport = _build_transport(handler)

    class _MockClient(httpx.AsyncClient):
        def __init__(self, *args: Any, **kwargs: Any):
            super().__init__(*args, transport=transport, **kwargs)

    monkeypatch.setattr(
        "nahida_bot.agent.providers.openai_compatible.httpx.AsyncClient", _MockClient
    )

    provider = OpenAICompatibleProvider(
        base_url="https://example.com/v1",
        api_key="x",
        model="test",
    )
    result = await provider.chat(
        messages=[ContextMessage(role="user", source="u", content="bad request")]
    )

    assert result.refusal == "I cannot help with that."
    assert result.finish_reason == "content_filter"


# ── ProviderResponse and TokenUsage dataclass ──


class TestTokenUsage:
    def test_total_property(self) -> None:
        usage = TokenUsage(input_tokens=100, output_tokens=50)
        assert usage.total == 150

    def test_defaults(self) -> None:
        usage = TokenUsage()
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0
        assert usage.cached_tokens == 0
        assert usage.reasoning_tokens == 0
        assert usage.total == 0


class TestProviderResponseExtendedFields:
    def test_default_new_fields(self) -> None:
        response = ProviderResponse(content="hi")
        assert response.reasoning_content is None
        assert response.reasoning_signature is None
        assert response.has_redacted_thinking is False
        assert response.refusal is None
        assert response.usage is None
        assert response.extra == {}

    def test_full_fields(self) -> None:
        usage = TokenUsage(input_tokens=10, output_tokens=20, reasoning_tokens=5)
        response = ProviderResponse(
            content="answer",
            reasoning_content="thinking chain",
            reasoning_signature="sig123",
            has_redacted_thinking=True,
            refusal=None,
            usage=usage,
            extra={"web_search": [{"title": "test"}]},
        )
        assert response.reasoning_content == "thinking chain"
        assert response.reasoning_signature == "sig123"
        assert response.has_redacted_thinking is True
        assert response.usage is not None
        assert response.usage.reasoning_tokens == 5
        assert "web_search" in response.extra


# ── ContextMessage reasoning fields ──


class TestContextMessageReasoningFields:
    def test_backward_compatible(self) -> None:
        msg = ContextMessage(role="user", source="u", content="hi")
        assert msg.reasoning is None
        assert msg.reasoning_signature is None
        assert msg.has_redacted_thinking is False

    def test_with_reasoning(self) -> None:
        msg = ContextMessage(
            role="assistant",
            source="provider_response",
            content="answer",
            reasoning="thinking",
            reasoning_signature="sig",
            has_redacted_thinking=True,
        )
        assert msg.reasoning == "thinking"
        assert msg.reasoning_signature == "sig"
        assert msg.has_redacted_thinking is True
