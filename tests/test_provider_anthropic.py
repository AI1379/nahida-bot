"""Unit tests for Anthropic Claude provider (Phase 2.8b)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from nahida_bot.agent.context import ContextMessage
from nahida_bot.agent.providers.anthropic import AnthropicProvider


def _build_transport(handler):  # noqa: ANN001
    return httpx.MockTransport(handler)


def _mock_anthropic_provider(monkeypatch, handler) -> AnthropicProvider:
    """Create an AnthropicProvider with mocked HTTP transport."""
    transport = _build_transport(handler)

    class _MockClient(httpx.AsyncClient):
        def __init__(self, *args: Any, **kwargs: Any):
            super().__init__(*args, transport=transport, **kwargs)

    monkeypatch.setattr(
        "nahida_bot.agent.providers.anthropic.httpx.AsyncClient", _MockClient
    )

    return AnthropicProvider(
        base_url="https://api.anthropic.com",
        api_key="test-key",
        model="claude-sonnet-4-20250514",
    )


# ── Text-only response ──


@pytest.mark.asyncio
async def test_anthropic_extracts_text_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider should extract text from content blocks."""

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello! How can I help you?"}],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 25, "output_tokens": 20},
        }
        return httpx.Response(200, json=payload)

    provider = _mock_anthropic_provider(monkeypatch, handler)
    result = await provider.chat(
        messages=[ContextMessage(role="user", source="u", content="hi")]
    )

    assert result.content == "Hello! How can I help you?"
    assert result.finish_reason == "stop"  # end_turn → stop
    assert result.reasoning_content is None
    assert result.reasoning_signature is None
    assert result.has_redacted_thinking is False


# ── Extended Thinking response ──


@pytest.mark.asyncio
async def test_anthropic_extracts_thinking_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider should extract thinking content and signature."""

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "thinking",
                    "thinking": "Let me analyze this step by step...",
                    "signature": "ErUB6pWIDo9Bkx_test",
                },
                {"type": "text", "text": "The answer is 42."},
            ],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 50, "output_tokens": 800},
        }
        return httpx.Response(200, json=payload)

    provider = _mock_anthropic_provider(monkeypatch, handler)
    result = await provider.chat(
        messages=[ContextMessage(role="user", source="u", content="test")]
    )

    assert result.content == "The answer is 42."
    assert result.reasoning_content == "Let me analyze this step by step..."
    assert result.reasoning_signature == "ErUB6pWIDo9Bkx_test"
    assert result.has_redacted_thinking is False


# ── Redacted Thinking response ──


@pytest.mark.asyncio
async def test_anthropic_handles_redacted_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider should flag redacted_thinking presence."""

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "thinking",
                    "thinking": "Normal thinking...",
                    "signature": "sig_normal",
                },
                {
                    "type": "redacted_thinking",
                    "signature": "sig_redacted",
                },
                {"type": "text", "text": "Based on my analysis..."},
            ],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 50, "output_tokens": 800},
        }
        return httpx.Response(200, json=payload)

    provider = _mock_anthropic_provider(monkeypatch, handler)
    result = await provider.chat(
        messages=[ContextMessage(role="user", source="u", content="test")]
    )

    assert result.content == "Based on my analysis..."
    assert result.reasoning_content == "Normal thinking..."
    assert result.has_redacted_thinking is True
    # Signature should be the last one seen (redacted_thinking comes last)
    assert result.reasoning_signature == "sig_redacted"


# ── Tool Use response ──


@pytest.mark.asyncio
async def test_anthropic_extracts_tool_use_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider should extract tool calls from tool_use blocks."""

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me look that up."},
                {
                    "type": "tool_use",
                    "id": "toolu_01A09q90qw90lq917635",
                    "name": "get_weather",
                    "input": {"location": "San Francisco, CA"},
                },
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 50, "output_tokens": 100},
        }
        return httpx.Response(200, json=payload)

    provider = _mock_anthropic_provider(monkeypatch, handler)
    result = await provider.chat(
        messages=[ContextMessage(role="user", source="u", content="weather?")]
    )

    assert result.content == "Let me look that up."
    assert result.finish_reason == "tool_calls"  # tool_use → tool_calls
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].call_id == "toolu_01A09q90qw90lq917635"
    assert result.tool_calls[0].name == "get_weather"
    assert result.tool_calls[0].arguments == {"location": "San Francisco, CA"}


# ── Interleaved Thinking ──


@pytest.mark.asyncio
async def test_anthropic_handles_interleaved_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider should merge interleaved thinking blocks."""

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "thinking",
                    "thinking": "User wants weather...",
                    "signature": "sig1",
                },
                {"type": "text", "text": "Let me check the weather."},
                {
                    "type": "tool_use",
                    "id": "toolu_01",
                    "name": "get_weather",
                    "input": {"city": "SF"},
                },
                {
                    "type": "thinking",
                    "thinking": "Now I have the data...",
                    "signature": "sig2",
                },
                {"type": "text", "text": "The weather in SF is sunny."},
            ],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 100, "output_tokens": 500},
        }
        return httpx.Response(200, json=payload)

    provider = _mock_anthropic_provider(monkeypatch, handler)
    result = await provider.chat(
        messages=[ContextMessage(role="user", source="u", content="weather?")]
    )

    # Text blocks merged
    assert "Let me check the weather." in result.content  # type: ignore[operator]
    assert "The weather in SF is sunny." in result.content  # type: ignore[operator]
    # Thinking blocks merged
    assert "User wants weather..." in result.reasoning_content  # type: ignore[operator]
    assert "Now I have the data..." in result.reasoning_content  # type: ignore[operator]
    # Last signature preserved
    assert result.reasoning_signature == "sig2"
    # Tool call present
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "get_weather"


# ── Serialization: system prompt extraction ──


@pytest.mark.asyncio
async def test_anthropic_extracts_system_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider should pass system messages as a separate 'system' parameter."""
    captured_payload: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_payload
        captured_payload = json.loads(request.content.decode("utf-8"))
        payload = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "done"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        return httpx.Response(200, json=payload)

    provider = _mock_anthropic_provider(monkeypatch, handler)
    await provider.chat(
        messages=[
            ContextMessage(role="system", source="s", content="You are helpful."),
            ContextMessage(role="user", source="u", content="hi"),
        ]
    )

    assert captured_payload["system"] == "You are helpful."
    # System message should NOT appear in messages array
    assert all(msg["role"] != "system" for msg in captured_payload["messages"])


# ── Serialization: thinking replay ──


@pytest.mark.asyncio
async def test_anthropic_serializes_thinking_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider should inject thinking blocks when replaying assistant history."""
    captured_payload: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_payload
        captured_payload = json.loads(request.content.decode("utf-8"))
        payload = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "done"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        return httpx.Response(200, json=payload)

    provider = _mock_anthropic_provider(monkeypatch, handler)
    await provider.chat(
        messages=[
            ContextMessage(role="user", source="u", content="hi"),
            ContextMessage(
                role="assistant",
                source="provider_response",
                content="The answer is 42.",
                reasoning="Step by step...",
                reasoning_signature="sig_original",
                has_redacted_thinking=True,
            ),
            ContextMessage(role="user", source="u", content="why?"),
        ]
    )

    # Check the assistant message in the serialized payload
    assistant_msg = captured_payload["messages"][1]
    assert assistant_msg["role"] == "assistant"

    blocks = assistant_msg["content"]
    # First block should be thinking with signature
    assert blocks[0]["type"] == "thinking"
    assert blocks[0]["thinking"] == "Step by step..."
    assert blocks[0]["signature"] == "sig_original"
    # Second block should be redacted_thinking
    assert blocks[1]["type"] == "redacted_thinking"
    assert blocks[1]["signature"] == "sig_original"
    # Third block should be text
    assert blocks[2]["type"] == "text"
    assert blocks[2]["text"] == "The answer is 42."


# ── format_tools ──


class TestAnthropicFormatTools:
    def test_uses_input_schema(self) -> None:
        from nahida_bot.agent.providers.base import ToolDefinition

        provider = AnthropicProvider(
            base_url="https://api.anthropic.com",
            api_key="x",
            model="test",
        )
        tools = provider.format_tools(
            [
                ToolDefinition(
                    name="test_tool",
                    description="A test tool",
                    parameters={
                        "type": "object",
                        "properties": {"q": {"type": "string"}},
                    },
                )
            ]
        )

        assert len(tools) == 1
        tool = tools[0]
        assert isinstance(tool, dict)
        assert tool["name"] == "test_tool"
        assert "input_schema" in tool
        assert "parameters" not in tool


# ── Usage parsing ──


@pytest.mark.asyncio
async def test_anthropic_parses_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider should extract usage statistics."""

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "hi"}],
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 20,
            },
        }
        return httpx.Response(200, json=payload)

    provider = _mock_anthropic_provider(monkeypatch, handler)
    result = await provider.chat(
        messages=[ContextMessage(role="user", source="u", content="hi")]
    )

    assert result.usage is not None
    assert result.usage.input_tokens == 100
    assert result.usage.output_tokens == 50
    assert result.usage.cached_tokens == 20


# ── Error mapping ──


@pytest.mark.asyncio
async def test_anthropic_auth_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nahida_bot.agent.providers.errors import ProviderAuthError

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    provider = _mock_anthropic_provider(monkeypatch, handler)
    with pytest.raises(ProviderAuthError):
        await provider.chat(
            messages=[ContextMessage(role="user", source="u", content="hi")]
        )


@pytest.mark.asyncio
async def test_anthropic_rate_limit_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nahida_bot.agent.providers.errors import ProviderRateLimitError

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limit"})

    provider = _mock_anthropic_provider(monkeypatch, handler)
    with pytest.raises(ProviderRateLimitError):
        await provider.chat(
            messages=[ContextMessage(role="user", source="u", content="hi")]
        )


# ── Refusal handling ──


@pytest.mark.asyncio
async def test_anthropic_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider should set refusal when stop_reason is 'refusal'."""

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "I cannot help with that."}],
            "stop_reason": "refusal",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        return httpx.Response(200, json=payload)

    provider = _mock_anthropic_provider(monkeypatch, handler)
    result = await provider.chat(
        messages=[ContextMessage(role="user", source="u", content="bad request")]
    )

    assert result.refusal is not None
    assert result.finish_reason == "content_filter"


# ── Provider registration ──


class TestAnthropicProviderRegistration:
    def test_registered_in_registry(self) -> None:
        from nahida_bot.agent.providers.registry import get_provider_class

        cls = get_provider_class("anthropic")
        assert cls is AnthropicProvider

    def test_api_family(self) -> None:
        provider = AnthropicProvider(
            base_url="https://api.anthropic.com",
            api_key="x",
            model="test",
        )
        assert provider.api_family == "anthropic-messages"
