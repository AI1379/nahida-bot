"""Unit tests for agent loop orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
import json

import pytest

from nahida_bot.agent.context import ContextBudget, ContextBuilder
from nahida_bot.agent.loop import (
    AgentLoop,
    AgentLoopConfig,
    ToolExecutionResult,
    ToolExecutor,
)
from nahida_bot.agent.providers import (
    ChatProvider,
    ProviderRateLimitError,
    ProviderResponse,
    ToolCall,
    ToolDefinition,
)
from nahida_bot.agent.tokenization import CharacterEstimateTokenizer


@dataclass
class _QueuedProvider(ChatProvider):
    responses: list[ProviderResponse] = field(default_factory=list)
    failures: list[Exception] = field(default_factory=list)
    calls: int = 0
    name: str = "queued-provider"

    @property
    def tokenizer(self):
        return None

    async def chat(self, *, messages, tools=None, timeout_seconds=None):  # noqa: ANN001
        self.calls += 1
        if self.failures:
            failure = self.failures.pop(0)
            raise failure

        if not self.responses:
            raise RuntimeError("No queued provider response")
        return self.responses.pop(0)


@dataclass
class _RecorderToolExecutor(ToolExecutor):
    calls: list[ToolCall] = field(default_factory=list)

    async def execute(self, tool_call: ToolCall) -> ToolExecutionResult:
        self.calls.append(tool_call)
        return ToolExecutionResult.success(
            output=f"result-for-{tool_call.name}",
            logs=["tool started", "tool completed"],
        )


@dataclass
class _QueuedToolExecutor(ToolExecutor):
    responses: list[ToolExecutionResult] = field(default_factory=list)
    calls: list[ToolCall] = field(default_factory=list)

    async def execute(self, tool_call: ToolCall) -> ToolExecutionResult:
        self.calls.append(tool_call)
        if not self.responses:
            raise RuntimeError("No queued tool response")
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_agent_loop_returns_direct_response_without_tools() -> None:
    """Loop should terminate immediately when provider returns plain content."""
    provider = _QueuedProvider(
        responses=[ProviderResponse(content="hello", tool_calls=[])]
    )
    builder = ContextBuilder(
        budget=ContextBudget(max_tokens=200, reserved_tokens=0),
        fallback_tokenizer=CharacterEstimateTokenizer(chars_per_token=20),
    )
    loop = AgentLoop(provider=provider, context_builder=builder)

    result = await loop.run(user_message="hi", system_prompt="sys")

    assert result.final_response == "hello"
    assert result.steps == 1
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_agent_loop_executes_tools_and_continues() -> None:
    """Loop should execute tool calls and continue to final assistant response."""
    provider = _QueuedProvider(
        responses=[
            ProviderResponse(
                content="calling tool",
                tool_calls=[
                    ToolCall(call_id="tc_1", name="read_file", arguments={"path": "a"})
                ],
            ),
            ProviderResponse(content="done", tool_calls=[]),
        ]
    )
    tool_executor = _RecorderToolExecutor()
    builder = ContextBuilder(
        budget=ContextBudget(max_tokens=240, reserved_tokens=0),
        fallback_tokenizer=CharacterEstimateTokenizer(chars_per_token=20),
    )
    loop = AgentLoop(
        provider=provider,
        context_builder=builder,
        tool_executor=tool_executor,
    )

    result = await loop.run(
        user_message="hi",
        system_prompt="sys",
        tools=[
            ToolDefinition(
                name="read_file",
                description="read",
                parameters={"type": "object", "properties": {}},
            )
        ],
    )

    assert result.final_response == "done"
    assert result.steps == 2
    assert len(tool_executor.calls) == 1
    assert result.tool_messages[0].source == "tool_result:read_file"
    payload = json.loads(result.tool_messages[0].content)
    assert payload["status"] == "ok"
    assert payload["output"] == "result-for-read_file"
    assert payload["logs"] == ["tool started", "tool completed"]
    assert result.tool_messages[0].metadata == {
        "tool_call_id": "tc_1",
        "tool_name": "read_file",
        "lifecycle": {"phase": "completed", "attempt": 1},
    }


@pytest.mark.asyncio
async def test_agent_loop_validates_tool_arguments_before_execution() -> None:
    """Loop should reject invalid tool arguments before executor is called."""
    provider = _QueuedProvider(
        responses=[
            ProviderResponse(
                content="calling tool",
                tool_calls=[ToolCall(call_id="tc_1", name="read_file", arguments={})],
            ),
            ProviderResponse(content="done", tool_calls=[]),
        ]
    )
    tool_executor = _RecorderToolExecutor()
    builder = ContextBuilder(
        budget=ContextBudget(max_tokens=240, reserved_tokens=0),
        fallback_tokenizer=CharacterEstimateTokenizer(chars_per_token=20),
    )
    loop = AgentLoop(
        provider=provider,
        context_builder=builder,
        tool_executor=tool_executor,
    )

    result = await loop.run(
        user_message="hi",
        system_prompt="sys",
        tools=[
            ToolDefinition(
                name="read_file",
                description="read",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            )
        ],
    )

    assert result.final_response == "done"
    assert tool_executor.calls == []
    payload = json.loads(result.tool_messages[0].content)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "tool_arguments_invalid"
    assert result.tool_messages[0].metadata == {
        "tool_call_id": "tc_1",
        "tool_name": "read_file",
        "lifecycle": {"phase": "prepare_failed", "attempt": 0},
    }


@pytest.mark.asyncio
async def test_agent_loop_retries_retryable_tool_errors() -> None:
    """Loop should retry retryable tool failures and keep final success result."""
    provider = _QueuedProvider(
        responses=[
            ProviderResponse(
                content="calling tool",
                tool_calls=[
                    ToolCall(call_id="tc_1", name="search", arguments={"q": "x"})
                ],
            ),
            ProviderResponse(content="done", tool_calls=[]),
        ]
    )
    tool_executor = _QueuedToolExecutor(
        responses=[
            ToolExecutionResult.error(
                code="tool_timeout",
                message="timeout",
                retryable=True,
                logs=["try-1 failed"],
            ),
            ToolExecutionResult.success(output={"items": ["ok"]}),
        ]
    )
    builder = ContextBuilder(
        budget=ContextBudget(max_tokens=260, reserved_tokens=0),
        fallback_tokenizer=CharacterEstimateTokenizer(chars_per_token=20),
    )
    loop = AgentLoop(
        provider=provider,
        context_builder=builder,
        tool_executor=tool_executor,
        config=AgentLoopConfig(
            tool_retry_attempts=1,
            tool_retry_backoff_seconds=0.0,
        ),
    )

    result = await loop.run(
        user_message="hi",
        system_prompt="sys",
        tools=[
            ToolDefinition(
                name="search",
                description="search",
                parameters={
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                    "required": ["q"],
                },
            )
        ],
    )

    assert result.final_response == "done"
    assert len(tool_executor.calls) == 2
    payload = json.loads(result.tool_messages[0].content)
    assert payload["status"] == "ok"
    assert payload["output"] == {"items": ["ok"]}
    assert result.tool_messages[0].metadata == {
        "tool_call_id": "tc_1",
        "tool_name": "search",
        "lifecycle": {"phase": "completed", "attempt": 2},
    }


@pytest.mark.asyncio
async def test_agent_loop_stops_retrying_non_retryable_tool_errors() -> None:
    """Loop should stop retrying when tool failure is marked non-retryable."""
    provider = _QueuedProvider(
        responses=[
            ProviderResponse(
                content="calling tool",
                tool_calls=[
                    ToolCall(call_id="tc_1", name="search", arguments={"q": "x"})
                ],
            ),
            ProviderResponse(content="done", tool_calls=[]),
        ]
    )
    tool_executor = _QueuedToolExecutor(
        responses=[
            ToolExecutionResult.error(
                code="tool_denied",
                message="permission denied",
                retryable=False,
                logs=["denied"],
            )
        ]
    )
    builder = ContextBuilder(
        budget=ContextBudget(max_tokens=260, reserved_tokens=0),
        fallback_tokenizer=CharacterEstimateTokenizer(chars_per_token=20),
    )
    loop = AgentLoop(
        provider=provider,
        context_builder=builder,
        tool_executor=tool_executor,
        config=AgentLoopConfig(
            tool_retry_attempts=3,
            tool_retry_backoff_seconds=0.0,
        ),
    )

    result = await loop.run(
        user_message="hi",
        system_prompt="sys",
        tools=[
            ToolDefinition(
                name="search",
                description="search",
                parameters={
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                    "required": ["q"],
                },
            )
        ],
    )

    assert result.final_response == "done"
    assert len(tool_executor.calls) == 1
    payload = json.loads(result.tool_messages[0].content)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "tool_denied"
    assert result.tool_messages[0].metadata == {
        "tool_call_id": "tc_1",
        "tool_name": "search",
        "lifecycle": {"phase": "failed", "attempt": 1},
    }


@pytest.mark.asyncio
async def test_agent_loop_records_assistant_tool_call_metadata() -> None:
    """Loop should keep assistant tool-call metadata for provider round-tripping."""
    provider = _QueuedProvider(
        responses=[
            ProviderResponse(
                content=None,
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(call_id="tc_1", name="search", arguments={"q": "x"})
                ],
            ),
            ProviderResponse(content="done", tool_calls=[]),
        ]
    )
    tool_executor = _QueuedToolExecutor(
        responses=[ToolExecutionResult.success(output={"items": ["ok"]})]
    )
    builder = ContextBuilder(
        budget=ContextBudget(max_tokens=260, reserved_tokens=0),
        fallback_tokenizer=CharacterEstimateTokenizer(chars_per_token=20),
    )
    loop = AgentLoop(
        provider=provider,
        context_builder=builder,
        tool_executor=tool_executor,
    )

    result = await loop.run(
        user_message="hi",
        system_prompt="sys",
        tools=[
            ToolDefinition(
                name="search",
                description="search",
                parameters={
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                    "required": ["q"],
                },
            )
        ],
    )

    assert result.final_response == "done"
    assert result.assistant_messages[0].metadata == {
        "finish_reason": "tool_calls",
        "tool_calls": [{"id": "tc_1", "name": "search", "arguments": {"q": "x"}}],
    }


@pytest.mark.asyncio
async def test_agent_loop_retries_retryable_provider_errors() -> None:
    """Loop should retry on retryable provider errors and eventually succeed."""
    provider = _QueuedProvider(
        responses=[ProviderResponse(content="ok", tool_calls=[])],
        failures=[ProviderRateLimitError()],
    )
    builder = ContextBuilder(
        budget=ContextBudget(max_tokens=200, reserved_tokens=0),
        fallback_tokenizer=CharacterEstimateTokenizer(chars_per_token=20),
    )
    loop = AgentLoop(
        provider=provider,
        context_builder=builder,
        config=AgentLoopConfig(retry_attempts=2, retry_backoff_seconds=0.0),
    )

    result = await loop.run(user_message="retry", system_prompt="sys")

    assert result.final_response == "ok"
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_agent_loop_raises_when_tool_requested_without_executor() -> None:
    """Loop should fail fast when provider requests tools but executor is missing."""
    provider = _QueuedProvider(
        responses=[
            ProviderResponse(
                content="need tool",
                tool_calls=[ToolCall(call_id="tc_1", name="x", arguments={})],
            )
        ]
    )
    builder = ContextBuilder(
        budget=ContextBudget(max_tokens=200, reserved_tokens=0),
        fallback_tokenizer=CharacterEstimateTokenizer(chars_per_token=20),
    )
    loop = AgentLoop(provider=provider, context_builder=builder)

    with pytest.raises(RuntimeError, match="no tool executor"):
        await loop.run(user_message="hi", system_prompt="sys")


def test_provider_contract_is_abstract() -> None:
    """ChatProvider should remain an abstract base class contract."""
    with pytest.raises(TypeError):
        ChatProvider()  # type: ignore[abstract]


def test_tool_executor_contract_is_abstract() -> None:
    """ToolExecutor should remain an abstract base class contract."""
    with pytest.raises(TypeError):
        ToolExecutor()  # type: ignore[abstract]
