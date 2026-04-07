"""Unit tests for OpenAI-compatible provider mapping and errors."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from nahida_bot.agent.context import ContextMessage
from nahida_bot.agent.providers import (
    OpenAICompatibleProvider,
    ProviderAuthError,
    ProviderBadResponseError,
    ProviderRateLimitError,
)


def _build_transport(handler):  # noqa: ANN001
    return httpx.MockTransport(handler)


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
