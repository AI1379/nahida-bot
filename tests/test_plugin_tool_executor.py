"""Tests for executing plugin-registered tools from the agent loop."""

from __future__ import annotations

import json

import pytest

from nahida_bot.agent.context import ContextBudget, ContextBuilder
from nahida_bot.agent.loop import AgentLoop
from nahida_bot.agent.providers import ChatProvider, ProviderResponse, ToolCall
from nahida_bot.agent.tokenization import CharacterEstimateTokenizer
from nahida_bot.plugins.registry import ToolEntry, ToolRegistry
from nahida_bot.plugins.tool_executor import RegistryToolExecutor


class _ToolCallingProvider(ChatProvider):
    name = "tool-calling-provider"

    def __init__(self) -> None:
        self.calls = 0

    @property
    def tokenizer(self):
        return None

    async def chat(self, *, messages, tools=None, timeout_seconds=None):  # noqa: ANN001
        self.calls += 1
        if self.calls == 1:
            assert tools is not None
            assert tools[0].name == "echo"
            return ProviderResponse(
                content=None,
                tool_calls=[
                    ToolCall(call_id="tc_1", name="echo", arguments={"text": "hi"})
                ],
            )

        tool_payload = json.loads(messages[-1].content)
        return ProviderResponse(
            content=f"tool said {tool_payload['output']}",
            tool_calls=[],
        )


@pytest.mark.asyncio
async def test_registry_tool_executor_completes_agent_tool_roundtrip() -> None:
    async def echo(text: str) -> str:
        return text.upper()

    registry = ToolRegistry()
    registry.register(
        ToolEntry(
            name="echo",
            description="Echo text",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            handler=echo,
            plugin_id="echo-plugin",
        )
    )
    executor = RegistryToolExecutor(registry)
    provider = _ToolCallingProvider()
    builder = ContextBuilder(
        budget=ContextBudget(max_tokens=300, reserved_tokens=0),
        fallback_tokenizer=CharacterEstimateTokenizer(chars_per_token=20),
    )
    loop = AgentLoop(
        provider=provider,
        context_builder=builder,
        tool_executor=executor,
    )

    result = await loop.run(
        user_message="call echo",
        system_prompt="sys",
        tools=executor.definitions(),
    )

    assert result.final_response == "tool said HI"
    assert result.steps == 2


@pytest.mark.asyncio
async def test_registry_tool_executor_reports_missing_tool() -> None:
    executor = RegistryToolExecutor(ToolRegistry())

    result = await executor.execute(
        ToolCall(call_id="tc_missing", name="missing", arguments={})
    )

    assert result.is_error is True
    assert result.error_code == "tool_not_registered"
