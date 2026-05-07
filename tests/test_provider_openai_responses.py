from __future__ import annotations

import pytest
from typing import Any, cast

from nahida_bot.agent.context import ContextBuilder, ContextMessage
from nahida_bot.agent.loop import AgentLoop
from nahida_bot.agent.providers.base import ProviderResponse, ToolCall, ToolDefinition
from nahida_bot.agent.providers.errors import ProviderBadResponseError
from nahida_bot.agent.providers.openai_responses import OpenAIResponsesProvider


class _FakeResponse:
    status_code: int = 200

    def __init__(self, body: dict[str, object], text: str = "") -> None:
        self._body = body
        self.text = text

    def json(self) -> dict[str, object]:
        return self._body


class _FakeClient:
    is_closed = False

    def __init__(self, body: dict[str, object], text: str = "") -> None:
        self.body = body
        self.text = text
        self.payload: dict[str, object] | None = None
        self.headers: dict[str, str] | None = None
        self.timeout: float | None = None
        self.url = ""

    async def post(
        self,
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> _FakeResponse:
        self.url = url
        self.payload = json
        self.headers = headers
        self.timeout = timeout
        return _FakeResponse(self.body, self.text)


def _provider(**kwargs: Any) -> OpenAIResponsesProvider:
    return OpenAIResponsesProvider(
        base_url="https://api.openai.test",
        api_key="test-key",
        model="gpt-test",
        **kwargs,
    )


def test_format_tools_maps_web_search_alias_and_keeps_functions() -> None:
    provider = _provider(built_in_tools=["web_search", "image_generation"])

    tools = provider.format_tools(
        [
            ToolDefinition(
                name="lookup",
                description="Lookup data",
                parameters={"type": "object", "properties": {}},
            )
        ]
    )

    assert tools == [
        {
            "type": "function",
            "name": "lookup",
            "description": "Lookup data",
            "parameters": {"type": "object", "properties": {}},
        },
        {"type": "web_search"},
        {"type": "image_generation"},
    ]


@pytest.mark.asyncio
async def test_chat_uses_previous_response_id_and_sends_only_new_input() -> None:
    provider = _provider(
        store_responses=True,
        use_previous_response_id=True,
        reasoning_effort="medium",
    )
    fake_client = _FakeClient(
        {
            "id": "resp_new",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "new answer"}],
                }
            ],
        }
    )
    provider._client = cast(Any, fake_client)

    response = await provider.chat(
        messages=[
            ContextMessage(
                role="system",
                source="system",
                content="Be concise.",
            ),
            ContextMessage(
                role="assistant",
                source="provider_response",
                content="old answer",
                metadata={"response_id": "resp_old"},
            ),
            ContextMessage(role="user", source="user_input", content="Next?"),
        ]
    )

    assert response.content == "new answer"
    assert fake_client.payload is not None
    assert fake_client.payload["previous_response_id"] == "resp_old"
    assert fake_client.payload["instructions"] == "**system**\n\nBe concise."
    assert fake_client.payload["store"] is True
    assert fake_client.payload["reasoning"] == {"effort": "medium"}
    assert fake_client.payload["input"] == [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "Next?"}],
        }
    ]


@pytest.mark.asyncio
async def test_store_responses_does_not_send_previous_response_id_by_default() -> None:
    provider = _provider(store_responses=True)
    fake_client = _FakeClient(
        {
            "id": "resp_new",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "new answer"}],
                }
            ],
        }
    )
    provider._client = cast(Any, fake_client)

    await provider.chat(
        messages=[
            ContextMessage(
                role="assistant",
                source="provider_response",
                content="old answer",
                metadata={"response_id": "resp_old"},
            ),
            ContextMessage(role="user", source="user_input", content="Next?"),
        ]
    )

    assert fake_client.payload is not None
    assert "previous_response_id" not in fake_client.payload
    assert fake_client.payload["store"] is True
    assert fake_client.payload["input"] == [
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "old answer"}],
        },
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "Next?"}],
        },
    ]


def test_parse_response_preserves_replay_output_and_generated_images() -> None:
    provider = _provider()

    response = provider._parse_response(
        {
            "id": "resp_123",
            "status": "completed",
            "output": [
                {
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "summary"}],
                },
                {
                    "type": "function_call",
                    "id": "fc_123",
                    "call_id": "call_123",
                    "name": "lookup",
                    "arguments": '{"q": "nahida"}',
                },
                {
                    "type": "image_generation_call",
                    "result": "base64-image",
                },
            ],
        }
    )

    assert response.tool_calls == [
        ToolCall(call_id="call_123", name="lookup", arguments={"q": "nahida"})
    ]
    assert response.reasoning_content == "summary"
    assert response.extra["response_id"] == "resp_123"
    assert response.extra["generated_images"] == [
        {"type": "image_generation_call", "data": "base64-image"}
    ]
    assert response.extra["response_output"] == [
        {
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "summary"}],
        },
        {
            "type": "function_call",
            "id": "fc_123",
            "call_id": "call_123",
            "name": "lookup",
            "arguments": '{"q": "nahida"}',
        },
    ]


def test_invalid_function_call_arguments_raise_bad_response() -> None:
    provider = _provider()

    with pytest.raises(ProviderBadResponseError):
        provider._parse_response(
            {
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "call_123",
                        "name": "lookup",
                        "arguments": "not-json",
                    }
                ],
            }
        )


def test_parse_response_accepts_top_level_output_text() -> None:
    provider = _provider()

    response = provider._parse_response(
        {
            "id": "resp_text",
            "status": "completed",
            "output_text": "top level answer",
            "output": [],
        }
    )

    assert response.content == "top level answer"


def test_parse_response_accepts_text_blocks_and_refusals() -> None:
    provider = _provider()

    text_response = provider._parse_response(
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "legacy text"}],
                }
            ],
        }
    )
    refusal_response = provider._parse_response(
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "refusal", "refusal": "cannot comply"}],
                }
            ],
        }
    )

    assert text_response.content == "legacy text"
    assert refusal_response.refusal == "cannot comply"


def test_parse_response_records_shape_when_text_is_empty() -> None:
    provider = _provider()

    response = provider._parse_response(
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "unknown_block", "value": "x"}],
                }
            ],
        }
    )

    assert response.content is None
    assert response.extra["response_shape"] == {
        "output_types": ["message"],
        "content_types": ["unknown_block"],
        "has_output_text": False,
    }


@pytest.mark.asyncio
async def test_chat_streaming_responses_collects_output_text() -> None:
    stream_body = "\n".join(
        [
            'data: {"type":"response.output_text.delta","delta":"Hi"}',
            'data: {"type":"response.output_text.delta","delta":" there"}',
            'data: {"type":"response.output_text.done","text":"Hi there"}',
            (
                'data: {"type":"response.output_item.done","item":'
                '{"type":"message","role":"assistant","content":'
                '[{"type":"output_text","text":"Hi there"}]}}'
            ),
            (
                'data: {"type":"response.completed","response":'
                '{"id":"resp_stream","status":"completed","output":[],'
                '"usage":{"input_tokens":1,"output_tokens":2}}}'
            ),
        ]
    )
    provider = _provider(stream_responses=True)
    fake_client = _FakeClient({}, stream_body)
    provider._client = cast(Any, fake_client)

    response = await provider.chat(
        messages=[ContextMessage(role="user", source="user_input", content="hi")]
    )

    assert response.content == "Hi there"
    assert response.extra["response_id"] == "resp_stream"
    assert fake_client.payload is not None
    assert fake_client.payload["stream"] is True
    assert fake_client.headers is not None
    assert fake_client.headers["Accept"] == "text/event-stream"


def test_agent_loop_keeps_response_metadata_and_image_only_content() -> None:
    loop = AgentLoop(provider=_provider(), context_builder=ContextBuilder())
    response = ProviderResponse(
        content=None,
        extra={
            "response_id": "resp_123",
            "response_output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                }
            ],
            "generated_images": [
                {"type": "image_generation_call", "data": "base64-image"}
            ],
        },
    )

    message = loop._build_assistant_message(response)

    assert message is not None
    assert message.content == "[generated image available]"
    assert message.metadata is not None
    assert message.metadata["response_id"] == "resp_123"
    assert message.metadata["generated_images"] == [
        {"type": "image_generation_call", "data": "base64-image"}
    ]


def test_agent_loop_keeps_builtin_tool_only_responses_visible() -> None:
    loop = AgentLoop(provider=_provider(), context_builder=ContextBuilder())
    response = ProviderResponse(
        content=None,
        extra={
            "response_id": "resp_456",
            "builtin_tool_calls": [{"type": "web_search_call", "status": "completed"}],
        },
    )

    message = loop._build_assistant_message(response)

    assert message is not None
    assert message.content == "[built-in tool output available]"
    assert message.metadata is not None
    assert message.metadata["builtin_tool_calls"] == [
        {"type": "web_search_call", "status": "completed"}
    ]


def test_agent_loop_makes_unknown_empty_output_visible() -> None:
    loop = AgentLoop(provider=_provider(), context_builder=ContextBuilder())
    response = ProviderResponse(
        content=None,
        extra={
            "response_shape": {
                "output_types": ["message"],
                "content_types": ["unknown_block"],
                "has_output_text": False,
            }
        },
    )

    assert loop._display_content(response) == "[empty response output received]"
