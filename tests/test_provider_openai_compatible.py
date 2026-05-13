"""Unit tests for OpenAI-compatible provider mapping and errors."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from nahida_bot.agent.context import ContextMessage, ContextPart
from nahida_bot.agent.providers import (
    OpenAICompatibleProvider,
    ProviderAuthError,
    ProviderBadResponseError,
    ProviderRateLimitError,
)


def _build_transport(handler):  # noqa: ANN001
    return httpx.MockTransport(handler)


def test_openai_serializes_multimodal_parts() -> None:
    provider = OpenAICompatibleProvider(
        base_url="https://example.com/v1",
        api_key="x",
        model="gpt-test",
    )

    payload = provider.serialize_messages(
        [
            ContextMessage(
                role="user",
                source="u",
                content="[image]",
                parts=[
                    ContextPart(type="text", text="describe this"),
                    ContextPart(
                        type="image_url",
                        url="https://example.com/img.jpg",
                        media_id="img_1",
                    ),
                    ContextPart(
                        type="image_base64",
                        data="abc123",
                        mime_type="image/png",
                        media_id="img_2",
                    ),
                ],
            )
        ]
    )

    content = payload[0]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "describe this"}
    assert content[1] == {
        "type": "image_url",
        "image_url": {"url": "https://example.com/img.jpg"},
    }
    assert content[2] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,abc123"},
    }


def test_openai_serializes_text_only_parts_as_plain_string() -> None:
    provider = OpenAICompatibleProvider(
        base_url="https://example.com/v1",
        api_key="x",
        model="gpt-test",
    )

    payload = provider.serialize_messages(
        [
            ContextMessage(
                role="user",
                source="u",
                content="fallback",
                parts=[
                    ContextPart(type="text", text="what is this?"),
                    ContextPart(
                        type="image_description",
                        text="A small diagram",
                        media_id="img_1",
                    ),
                ],
            )
        ]
    )

    assert payload[0]["content"] == "what is this?\nA small diagram"


@pytest.mark.asyncio
async def test_openai_provider_parses_content_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider should parse assistant content from OpenAI-compatible payload."""

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "hello"},
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
        model="gpt-test",
    )
    result = await provider.chat(
        messages=[ContextMessage(role="user", source="u", content="hi")]
    )

    assert result.content == "hello"
    assert result.finish_reason == "stop"


@pytest.mark.asyncio
async def test_openai_provider_parses_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider should parse tool calls and JSON arguments."""

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {
                                    "name": "search",
                                    "arguments": json.dumps({"q": "nahida"}),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
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
        model="gpt-test",
    )
    result = await provider.chat(
        messages=[ContextMessage(role="user", source="u", content="hi")]
    )

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "search"
    assert result.tool_calls[0].arguments == {"q": "nahida"}


@pytest.mark.asyncio
async def test_openai_provider_streaming_collects_content_and_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Streaming mode should aggregate SSE deltas into the normal response shape."""
    captured_payload: dict[str, Any] = {}
    captured_accept = ""

    stream_body = "\n".join(
        [
            'data: {"choices":[{"delta":{"content":"Let me "},"finish_reason":null}]}',
            (
                'data: {"choices":[{"delta":{"reasoning_content":"thinking"},'
                '"finish_reason":null}]}'
            ),
            (
                'data: {"choices":[{"delta":{"content":"check.",'
                '"tool_calls":[{"index":0,"id":"call_1","type":"function",'
                '"function":{"name":"search","arguments":"{\\"q\\":"}}]},'
                '"finish_reason":null}]}'
            ),
            (
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
                '"function":{"arguments":"\\"nahida\\"}"}}]},'
                '"finish_reason":"tool_calls"}]}'
            ),
            "data: [DONE]",
        ]
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_payload, captured_accept
        captured_payload = json.loads(request.content.decode("utf-8"))
        captured_accept = request.headers["Accept"]
        return httpx.Response(200, text=stream_body)

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
        model="gpt-test",
        stream_responses=True,
    )
    result = await provider.chat(
        messages=[ContextMessage(role="user", source="u", content="hi")]
    )

    assert captured_payload["stream"] is True
    assert captured_accept == "text/event-stream"
    assert result.content == "Let me check."
    assert result.reasoning_content == "thinking"
    assert result.finish_reason == "tool_calls"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].call_id == "call_1"
    assert result.tool_calls[0].name == "search"
    assert result.tool_calls[0].arguments == {"q": "nahida"}


@pytest.mark.asyncio
async def test_openai_provider_maps_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider should map 401/403 to normalized auth error."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

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
        model="gpt-test",
    )

    with pytest.raises(ProviderAuthError):
        await provider.chat(
            messages=[ContextMessage(role="user", source="u", content="hi")]
        )


@pytest.mark.asyncio
async def test_openai_provider_maps_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider should map 429 to normalized rate-limit error."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "too many requests"})

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
        model="gpt-test",
    )

    with pytest.raises(ProviderRateLimitError):
        await provider.chat(
            messages=[ContextMessage(role="user", source="u", content="hi")]
        )


@pytest.mark.asyncio
async def test_openai_provider_rejects_invalid_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider should reject malformed completion payloads."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

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
        model="gpt-test",
    )

    with pytest.raises(ProviderBadResponseError):
        await provider.chat(
            messages=[ContextMessage(role="user", source="u", content="hi")]
        )


@pytest.mark.asyncio
async def test_openai_provider_serializes_tool_message_tool_call_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider should include tool_call_id when serializing tool messages."""
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
        model="gpt-test",
    )
    await provider.chat(
        messages=[
            ContextMessage(role="user", source="u", content="hi"),
            ContextMessage(
                role="assistant",
                source="provider_response",
                content="",
                metadata={
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "name": "search",
                            "arguments": {"q": "nahida"},
                        }
                    ]
                },
            ),
            ContextMessage(
                role="tool",
                source="tool_result:search",
                content='{"status":"ok"}',
                metadata={"tool_call_id": "call_1", "tool_name": "search"},
            ),
        ]
    )

    tool_message = captured_payload["messages"][2]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "call_1"


def test_openai_provider_drops_orphan_tool_messages() -> None:
    """Provider should not send tool messages without preceding tool_calls."""
    provider = OpenAICompatibleProvider(
        base_url="https://example.com/v1",
        api_key="x",
        model="gpt-test",
    )

    payload = provider.serialize_messages(
        [
            ContextMessage(role="user", source="u", content="hi"),
            ContextMessage(
                role="tool",
                source="tool_result:search",
                content='{"status":"ok"}',
                metadata={"tool_call_id": "call_1", "tool_name": "search"},
            ),
        ]
    )

    assert [message["role"] for message in payload] == ["user"]


def test_openai_provider_drops_incomplete_tool_call_groups() -> None:
    """Provider should drop assistant tool_calls when not all tool results remain."""
    provider = OpenAICompatibleProvider(
        base_url="https://example.com/v1",
        api_key="x",
        model="gpt-test",
    )

    payload = provider.serialize_messages(
        [
            ContextMessage(role="user", source="u", content="hi"),
            ContextMessage(
                role="assistant",
                source="provider_response",
                content="checking",
                metadata={
                    "tool_calls": [
                        {"id": "call_1", "name": "search", "arguments": {}},
                        {"id": "call_2", "name": "read_file", "arguments": {}},
                    ]
                },
            ),
            ContextMessage(
                role="tool",
                source="tool_result:search",
                content='{"status":"ok"}',
                metadata={"tool_call_id": "call_1", "tool_name": "search"},
            ),
            ContextMessage(role="user", source="u", content="next"),
        ]
    )

    assert [message["role"] for message in payload] == ["user", "user"]


@pytest.mark.asyncio
async def test_openai_provider_serializes_assistant_tool_calls_from_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider should serialize assistant tool calls when metadata exists."""
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
        model="gpt-test",
    )
    await provider.chat(
        messages=[
            ContextMessage(role="user", source="u", content="hi"),
            ContextMessage(
                role="assistant",
                source="provider_response",
                content="",
                metadata={
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "name": "search",
                            "arguments": {"q": "nahida"},
                        }
                    ]
                },
            ),
            ContextMessage(
                role="tool",
                source="tool_result:search",
                content='{"status":"ok"}',
                metadata={"tool_call_id": "call_1", "tool_name": "search"},
            ),
        ]
    )

    assistant_message = captured_payload["messages"][1]
    assert assistant_message["role"] == "assistant"
    assert assistant_message["content"] is None
    assert assistant_message["tool_calls"][0]["id"] == "call_1"
    assert assistant_message["tool_calls"][0]["function"]["name"] == "search"
    assert (
        assistant_message["tool_calls"][0]["function"]["arguments"] == '{"q": "nahida"}'
    )
