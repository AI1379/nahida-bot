"""Live integration tests for real tool-calling backend roundtrips.

These tests intentionally use real backend credentials from `live_llm_config`
and are skipped when env vars are not provided.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from nahida_bot.agent.context import ContextBudget, ContextBuilder
from nahida_bot.agent.context import ContextMessage
from nahida_bot.agent.loop import (
    AgentLoop,
    AgentLoopConfig,
    ToolExecutionResult,
    ToolExecutor,
)
from nahida_bot.agent.providers import (
    OpenAICompatibleProvider,
    ToolCall,
    ToolDefinition,
)
from nahida_bot.agent.tokenization import CharacterEstimateTokenizer


@dataclass
class _LiveRecorderToolExecutor(ToolExecutor):
    calls: list[ToolCall] = field(default_factory=list)

    async def execute(self, tool_call: ToolCall) -> ToolExecutionResult:
        self.calls.append(tool_call)
        query = tool_call.arguments.get("q")
        return ToolExecutionResult.success(
            output={"echo_query": query, "source": "integration-live-tool"},
            logs=["tool received request", "tool generated response"],
        )


@pytest.mark.integration
@pytest.mark.network
@pytest.mark.slow
@pytest.mark.live
@pytest.mark.asyncio
async def test_live_tool_calling_roundtrip_with_real_backend(live_llm_config):
    """Use a real OpenAI-compatible backend and verify tool-calling roundtrip."""
    if live_llm_config is None:
        pytest.skip("Live LLM config is not provided")

    provider = OpenAICompatibleProvider(
        base_url=live_llm_config["base_url"],
        api_key=live_llm_config["api_key"],
        model=live_llm_config["model"],
    )
    context_builder = ContextBuilder(
        budget=ContextBudget(max_tokens=4096, reserved_tokens=512),
        fallback_tokenizer=CharacterEstimateTokenizer(chars_per_token=20),
    )
    tool_executor = _LiveRecorderToolExecutor()
    loop = AgentLoop(
        provider=provider,
        context_builder=context_builder,
        tool_executor=tool_executor,
        config=AgentLoopConfig(
            max_steps=4,
            provider_timeout_seconds=45,
            tool_retry_attempts=1,
            tool_retry_backoff_seconds=0.0,
        ),
    )

    result = await loop.run(
        system_prompt=(
            "You are a strict agent. If tool `search` is available, call it exactly "
            "once before giving your final answer."
        ),
        user_message=(
            'Please use the `search` tool with argument {"q":"nahida"}. '
            "After tool result, answer with a short sentence that contains DONE."
        ),
        tools=[
            ToolDefinition(
                name="search",
                description="Search integration data.",
                parameters={
                    "type": "object",
                    "properties": {
                        "q": {"type": "string"},
                    },
                    "required": ["q"],
                    "additionalProperties": False,
                },
            )
        ],
    )

    assert result.final_response
    assert "done" in result.final_response.lower()

    # Real backend behavior can vary, but a successful tool-call loop must
    # produce at least one executed tool call and one structured tool message.
    assert len(tool_executor.calls) >= 1
    assert len(result.tool_messages) >= 1

    tool_message = result.tool_messages[-1]
    assert tool_message.role == "tool"
    assert tool_message.metadata is not None
    assert "tool_call_id" in tool_message.metadata
    assert tool_message.metadata.get("tool_name") == "search"

    payload = json.loads(tool_message.content)
    assert payload["status"] == "ok"
    assert isinstance(payload["output"], dict)
    assert payload["output"].get("source") == "integration-live-tool"


@pytest.mark.integration
@pytest.mark.network
@pytest.mark.slow
@pytest.mark.live
@pytest.mark.asyncio
async def test_live_tool_calling_provider_contract_with_real_backend(live_llm_config):
    """Validate provider contract for live tool-capable responses."""
    if live_llm_config is None:
        pytest.skip("Live LLM config is not provided")

    provider = OpenAICompatibleProvider(
        base_url=live_llm_config["base_url"],
        api_key=live_llm_config["api_key"],
        model=live_llm_config["model"],
    )

    response = await provider.chat(
        messages=[
            ContextMessage(
                role="user",
                source="integration_live_tool_contract",
                content=(
                    'If tools are available, call `search` with {"q":"nahida"}. '
                    "If not, reply with a short sentence."
                ),
            )
        ],
        tools=[
            ToolDefinition(
                name="search",
                description="Search integration data.",
                parameters={
                    "type": "object",
                    "properties": {
                        "q": {"type": "string"},
                    },
                    "required": ["q"],
                    "additionalProperties": False,
                },
            )
        ],
        timeout_seconds=45,
    )

    assert response.raw_response is not None
    assert response.content is None or isinstance(response.content, str)
    assert isinstance(response.tool_calls, list)

    for call in response.tool_calls:
        assert call.call_id
        assert call.name == "search"
        assert isinstance(call.arguments, dict)
