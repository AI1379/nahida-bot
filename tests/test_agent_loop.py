"""Unit tests for agent loop orchestration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json

import pytest

from nahida_bot.agent.context import (
    ContextBudget,
    ContextBuilder,
    ContextMessage,
    ContextPart,
)
from nahida_bot.agent.loop import (
    AgentLoop,
    AgentLoopConfig,
    ToolExecutionResult,
    ToolExecutor,
)
from nahida_bot.agent.metrics import MetricsCollector
from nahida_bot.agent.providers import (
    ChatProvider,
    ProviderAuthError,
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
    observed_messages: list[list[ContextMessage]] = field(default_factory=list)
    calls: int = 0
    name: str = "queued-provider"

    @property
    def tokenizer(self):
        return None

    async def _chat_impl(
        self, *, messages, tools=None, timeout_seconds=None, model=None
    ):  # noqa: ANN001
        self.calls += 1
        self.observed_messages.append(list(messages))
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


@dataclass
class _HangingToolExecutor(ToolExecutor):
    calls: list[ToolCall] = field(default_factory=list)

    async def execute(self, tool_call: ToolCall) -> ToolExecutionResult:
        self.calls.append(tool_call)
        await asyncio.sleep(60)
        return ToolExecutionResult.success(output="too late")


@dataclass
class _BlockingProvider(ChatProvider):
    """Provider that blocks inside ``chat`` until ``proceed`` is set.

    Simulates a long model generation so the stop-interruption path can be
    exercised: when the loop cancels the in-flight call, ``cancelled`` flips.
    """

    name: str = "blocking-provider"
    chat_started: asyncio.Event = field(default_factory=asyncio.Event)
    proceed: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled: bool = False
    completed: bool = False

    @property
    def tokenizer(self):  # noqa: D102
        return None

    async def _chat_impl(  # noqa: ANN001
        self, *, messages, tools=None, timeout_seconds=None, model=None
    ):
        self.chat_started.set()
        try:
            await self.proceed.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        self.completed = True
        return ProviderResponse(content="should not be reached", tool_calls=[])


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
async def test_agent_loop_passes_user_parts_to_provider() -> None:
    provider = _QueuedProvider(
        responses=[ProviderResponse(content="looks good", tool_calls=[])]
    )
    builder = ContextBuilder(
        budget=ContextBudget(max_tokens=300, reserved_tokens=0),
        fallback_tokenizer=CharacterEstimateTokenizer(chars_per_token=20),
    )
    loop = AgentLoop(provider=provider, context_builder=builder)

    await loop.run(
        user_message="describe this",
        user_parts=[
            ContextPart(type="text", text="describe this"),
            ContextPart(
                type="image_url",
                url="https://example.com/image.jpg",
                media_id="img_1",
            ),
        ],
        system_prompt="sys",
    )

    user_messages = [
        msg for msg in provider.observed_messages[0] if msg.source == "user_input"
    ]
    assert len(user_messages) == 1
    assert [part.type for part in user_messages[0].parts] == ["text", "image_url"]


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
    tool_messages = [
        message for message in provider.observed_messages[1] if message.role == "tool"
    ]
    assert len(tool_messages) == 1
    assert tool_messages[0].metadata is not None
    assert tool_messages[0].metadata["tool_call_id"] == "tc_1"


@pytest.mark.asyncio
async def test_agent_loop_supports_multiple_tool_rounds() -> None:
    """Loop should allow a provider to request tools across multiple steps."""
    provider = _QueuedProvider(
        responses=[
            ProviderResponse(
                content="first tool",
                tool_calls=[
                    ToolCall(call_id="tc_1", name="search", arguments={"q": "x"})
                ],
            ),
            ProviderResponse(
                content="second tool",
                tool_calls=[
                    ToolCall(call_id="tc_2", name="read_file", arguments={"path": "a"})
                ],
            ),
            ProviderResponse(content="final", tool_calls=[]),
        ]
    )
    tool_executor = _RecorderToolExecutor()
    builder = ContextBuilder(
        budget=ContextBudget(max_tokens=400, reserved_tokens=0),
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
            ),
            ToolDefinition(
                name="read_file",
                description="read",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            ),
        ],
    )

    assert result.final_response == "final"
    assert result.steps == 3
    assert [call.name for call in tool_executor.calls] == ["search", "read_file"]
    assert [message.metadata["tool_call_id"] for message in result.tool_messages] == [  # type: ignore
        "tc_1",
        "tc_2",
    ]
    assert provider.observed_messages[2][-2].role == "assistant"
    assert provider.observed_messages[2][-1].role == "tool"


@pytest.mark.asyncio
async def test_agent_loop_preserves_active_tool_transcript_when_budget_is_tight() -> (
    None
):
    """A large tool result should be truncated, not dropped before the next step."""
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
        responses=[ToolExecutionResult.success(output="x" * 500)]
    )
    builder = ContextBuilder(
        budget=ContextBudget(max_tokens=80, reserved_tokens=0, summary_max_chars=80),
        fallback_tokenizer=CharacterEstimateTokenizer(chars_per_token=10),
    )
    loop = AgentLoop(
        provider=provider,
        context_builder=builder,
        tool_executor=tool_executor,
    )

    result = await loop.run(
        user_message="task details",
        system_prompt="sys",
        history_messages=[
            ContextMessage(role="user", source="history", content="old " * 80)
        ],
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
    second_prompt = provider.observed_messages[1]
    assert [message.source for message in second_prompt] == [
        "system_baseline",
        "user_input",
        "provider_response",
        "tool_result:search",
    ]
    tool_message = second_prompt[-1]
    assert tool_message.metadata is not None
    assert tool_message.metadata["tool_call_id"] == "tc_1"
    assert len(tool_message.content) < len(result.tool_messages[0].content)


@pytest.mark.asyncio
async def test_agent_loop_injects_tool_use_guidance_when_tools_are_available() -> None:
    provider = _QueuedProvider(responses=[ProviderResponse(content="ok")])
    builder = ContextBuilder(
        budget=ContextBudget(max_tokens=300, reserved_tokens=0),
        fallback_tokenizer=CharacterEstimateTokenizer(chars_per_token=20),
    )
    loop = AgentLoop(provider=provider, context_builder=builder)

    await loop.run(
        user_message="hi",
        system_prompt="sys",
        tools=[
            ToolDefinition(
                name="search",
                description="search",
                parameters={"type": "object", "properties": {}},
            )
        ],
    )

    system_messages = [
        message for message in provider.observed_messages[0] if message.role == "system"
    ]
    assert len(system_messages) == 1
    assert "structured tool/function calling interface" in system_messages[0].content


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
async def test_agent_loop_times_out_hanging_tool_and_continues() -> None:
    """A stuck tool should be converted into a tool-result error for the model."""
    provider = _QueuedProvider(
        responses=[
            ProviderResponse(
                content="calling tool",
                tool_calls=[
                    ToolCall(call_id="tc_1", name="search", arguments={"q": "x"})
                ],
            ),
            ProviderResponse(content="done after timeout", tool_calls=[]),
        ]
    )
    tool_executor = _HangingToolExecutor()
    builder = ContextBuilder(
        budget=ContextBudget(max_tokens=260, reserved_tokens=0),
        fallback_tokenizer=CharacterEstimateTokenizer(chars_per_token=20),
    )
    loop = AgentLoop(
        provider=provider,
        context_builder=builder,
        tool_executor=tool_executor,
        config=AgentLoopConfig(
            tool_timeout_seconds=0.01,
            tool_retry_attempts=0,
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

    assert result.final_response == "done after timeout"
    assert result.steps == 2
    assert len(tool_executor.calls) == 1
    payload = json.loads(result.tool_messages[0].content)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "tool_timeout"


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


# ---------------------------------------------------------------------------
# Phase 2.6 — Fallback on provider error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_loop_fallback_on_non_retryable_provider_error() -> None:
    """Loop should return a fallback result instead of raising on non-retryable errors."""
    provider = _QueuedProvider(
        failures=[ProviderAuthError()],
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

    result = await loop.run(user_message="hi", system_prompt="sys")

    assert result.error == "provider_auth_failed"
    assert "provider_auth_failed" in result.final_response
    assert result.steps == 1  # Failed at step 1 (first provider call).


@pytest.mark.asyncio
async def test_agent_loop_fallback_preserves_prior_assistant_messages() -> None:
    """Fallback should include any assistant messages produced before the error."""
    provider = _QueuedProvider(
        responses=[
            ProviderResponse(
                content="calling tool",
                tool_calls=[
                    ToolCall(call_id="tc_1", name="search", arguments={"q": "x"})
                ],
            ),
        ],
        failures=[ProviderAuthError()],
    )
    provider.calls = 0
    # First call succeeds (returns tool_calls), second call fails (auth error).
    # We need the failures to only trigger on the 2nd call.
    provider.failures = []
    provider.responses = [
        ProviderResponse(
            content="calling tool",
            tool_calls=[ToolCall(call_id="tc_1", name="search", arguments={"q": "x"})],
        ),
    ]
    # Use a custom provider that fails on the second call.
    call_count = 0

    @dataclass
    class _TwoPhaseProvider(ChatProvider):
        name: str = "two-phase"

        @property
        def tokenizer(self):
            return None

        async def _chat_impl(
            self, *, messages, tools=None, timeout_seconds=None, model=None
        ):  # noqa: ANN001
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ProviderResponse(
                    content="partial",
                    tool_calls=[
                        ToolCall(call_id="tc_1", name="search", arguments={"q": "x"})
                    ],
                )
            raise ProviderAuthError()

    tool_executor = _QueuedToolExecutor(
        responses=[ToolExecutionResult.success(output="ok")]
    )
    builder = ContextBuilder(
        budget=ContextBudget(max_tokens=300, reserved_tokens=0),
        fallback_tokenizer=CharacterEstimateTokenizer(chars_per_token=20),
    )
    loop = AgentLoop(
        provider=_TwoPhaseProvider(),
        context_builder=builder,
        tool_executor=tool_executor,
        config=AgentLoopConfig(retry_attempts=0),
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

    assert result.error == "provider_auth_failed"
    assert result.steps == 2  # Failed at step 2 (second provider call).
    assert len(result.assistant_messages) == 1
    assert result.assistant_messages[0].content == "partial"
    assert "partial" in result.final_response


# ---------------------------------------------------------------------------
# Phase 2.6 — Metrics integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_loop_records_provider_metrics() -> None:
    """Loop should record provider call latency in the metrics collector."""
    metrics = MetricsCollector()
    provider = _QueuedProvider(
        responses=[ProviderResponse(content="hello", tool_calls=[])]
    )
    builder = ContextBuilder(
        budget=ContextBudget(max_tokens=200, reserved_tokens=0),
        fallback_tokenizer=CharacterEstimateTokenizer(chars_per_token=20),
    )
    loop = AgentLoop(provider=provider, context_builder=builder, metrics=metrics)

    result = await loop.run(user_message="hi", system_prompt="sys")

    assert result.trace_id is not None
    assert metrics.trace_count == 1
    stats = metrics.provider_latency_stats()
    assert stats["count"] == 1.0
    assert stats["min"] >= 0.0


@pytest.mark.asyncio
async def test_agent_loop_records_tool_metrics() -> None:
    """Loop should record tool call metrics in the metrics collector."""
    metrics = MetricsCollector()
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
        metrics=metrics,
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

    assert result.trace_id is not None
    assert metrics.tool_success_rate() == 1.0
    assert metrics.provider_latency_stats()["count"] == 2.0  # Two provider calls.


@pytest.mark.asyncio
async def test_agent_loop_records_provider_error_metrics() -> None:
    """Loop should record provider errors in the metrics collector."""
    metrics = MetricsCollector()
    provider = _QueuedProvider(
        failures=[ProviderAuthError()],
    )
    builder = ContextBuilder(
        budget=ContextBudget(max_tokens=200, reserved_tokens=0),
        fallback_tokenizer=CharacterEstimateTokenizer(chars_per_token=20),
    )
    loop = AgentLoop(
        provider=provider,
        context_builder=builder,
        config=AgentLoopConfig(retry_attempts=0),
        metrics=metrics,
    )

    result = await loop.run(user_message="hi", system_prompt="sys")

    assert result.error is not None
    assert metrics.provider_error_rate() == 1.0


@pytest.mark.asyncio
async def test_agent_loop_no_metrics_when_collector_not_provided() -> None:
    """Loop should work without metrics collector (backward compatibility)."""
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
    assert result.trace_id is None


# ---------------------------------------------------------------------------
# Graceful stop via stop_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_loop_stop_event_preserves_partial_messages() -> None:
    """A graceful stop must carry the partial transcript out via the done event.

    Regression guard for the /stop context-loss fix: when ``stop_event`` is
    set mid-run, the loop must exit through its ``done`` path holding whatever
    assistant and tool messages were produced so far, instead of being
    cancelled (which would drop them and skip persistence downstream).
    """
    provider = _QueuedProvider(
        responses=[
            ProviderResponse(
                content="partial",
                tool_calls=[
                    ToolCall(call_id="tc_1", name="search", arguments={"q": "x"})
                ],
            ),
            ProviderResponse(content="should not be reached", tool_calls=[]),
        ]
    )
    tool_executor = _RecorderToolExecutor()
    builder = ContextBuilder(
        budget=ContextBudget(max_tokens=300, reserved_tokens=0),
        fallback_tokenizer=CharacterEstimateTokenizer(chars_per_token=20),
    )
    loop = AgentLoop(
        provider=provider,
        context_builder=builder,
        tool_executor=tool_executor,
    )
    stop_event = asyncio.Event()

    done_event = None
    async for event in loop.run_stream(
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
        stop_event=stop_event,
    ):
        if event.type == "tool_end":
            stop_event.set()
        if event.type == "done":
            done_event = event

    assert done_event is not None
    assert done_event.error == "cancelled"
    assert [message.content for message in done_event.assistant_messages] == ["partial"]
    assert [message.source for message in done_event.tool_messages] == [
        "tool_result:search"
    ]
    # The loop stopped before consuming the second queued provider response.
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_agent_loop_stop_event_interrupts_inflight_provider_call() -> None:
    """stop_event set during ``provider.chat()`` aborts the in-flight call.

    Regression for the production symptom where ``/stop`` set 2s into an 18.5s
    call was honored only after the call finished: the provider call was a
    single uninterruptible await. Now the call is raced against ``stop_event``
    and the HTTP task is cancelled, so the loop emits a cancelled ``done``
    event promptly. Also asserts the bug-B fix: cancelled done-events carry
    ``trace_id`` (previously omitted, which skipped transcript persistence).
    """
    provider = _BlockingProvider()
    builder = ContextBuilder(
        budget=ContextBudget(max_tokens=300, reserved_tokens=0),
        fallback_tokenizer=CharacterEstimateTokenizer(chars_per_token=20),
    )
    metrics = MetricsCollector()
    loop = AgentLoop(
        provider=provider,
        context_builder=builder,
        metrics=metrics,
    )
    stop_event = asyncio.Event()

    async def _stop_once_chat_starts() -> None:
        await provider.chat_started.wait()
        stop_event.set()

    asyncio.create_task(_stop_once_chat_starts())

    done_event = None
    event_loop = asyncio.get_running_loop()
    t0 = event_loop.time()
    async for event in loop.run_stream(
        user_message="hi", system_prompt="sys", stop_event=stop_event
    ):
        if event.type == "done":
            done_event = event
    elapsed = event_loop.time() - t0

    assert done_event is not None
    assert done_event.error == "cancelled"
    # Bug-B fix: cancelled done-events now carry trace_id so transcript persist
    # + ledger join work for cancelled runs.
    assert done_event.trace_id
    # The user turn is still carried so SessionRunner persists it (#28).
    assert done_event.ordered_transcript
    # The blocking call was actually interrupted, not run to completion.
    assert provider.cancelled is True
    assert provider.completed is False
    # Interrupted promptly — without the race this hangs ~provider_timeout (120s).
    assert elapsed < 5.0
    assert metrics.terminal_outcome_counts() == {"cancelled:cancelled": 1}


@pytest.mark.asyncio
async def test_agent_loop_stop_event_during_retry_backoff_aborts() -> None:
    """Stop set during provider retry backoff aborts instead of retrying.

    The backoff sleep is a second uninterruptible await; it must also honor
    stop so /stop during a retry storm is prompt.
    """
    provider = _QueuedProvider(
        responses=[ProviderResponse(content="after retry", tool_calls=[])],
        failures=[ProviderRateLimitError()],
    )
    builder = ContextBuilder(
        budget=ContextBudget(max_tokens=300, reserved_tokens=0),
        fallback_tokenizer=CharacterEstimateTokenizer(chars_per_token=20),
    )
    loop = AgentLoop(
        provider=provider,
        context_builder=builder,
        config=AgentLoopConfig(retry_attempts=2, retry_backoff_seconds=5.0),
    )
    stop_event = asyncio.Event()

    async def _stop_shortly() -> None:
        await asyncio.sleep(0.1)
        stop_event.set()

    asyncio.create_task(_stop_shortly())

    done_event = None
    async for event in loop.run_stream(
        user_message="hi", system_prompt="sys", stop_event=stop_event
    ):
        if event.type == "done":
            done_event = event

    assert done_event is not None
    assert done_event.error == "cancelled"
    # First (failing) call consumed the failure; the backoff was interrupted
    # before the retry, so the provider was never called a second time.
    assert provider.calls == 1


# ---------------------------------------------------------------------------
# Phase 0 — terminal-outcome telemetry (observability only, no behaviour change)
# ---------------------------------------------------------------------------


def _metrics_builder() -> ContextBuilder:
    return ContextBuilder(
        budget=ContextBudget(max_tokens=300, reserved_tokens=0),
        fallback_tokenizer=CharacterEstimateTokenizer(chars_per_token=20),
    )


@pytest.mark.asyncio
async def test_terminal_outcome_plain_answer_is_completed_no_tool_calls() -> None:
    """A plain answer with no tool call records completed/no_tool_calls.

    This is the #21 candidate pool: runs that claim completion without ever
    calling a tool. Phase 0 only *counts* them; it must not change the result.
    """
    metrics = MetricsCollector()
    provider = _QueuedProvider(
        responses=[ProviderResponse(content="hello", tool_calls=[])]
    )
    loop = AgentLoop(
        provider=provider, context_builder=_metrics_builder(), metrics=metrics
    )

    result = await loop.run(user_message="hi", system_prompt="sys")

    assert result.final_response == "hello"  # behaviour unchanged
    assert result.trace_id is not None
    assert metrics.terminal_outcome_counts() == {"completed:no_tool_calls": 1}
    assert metrics.protocol_anomaly_total() == 0


@pytest.mark.asyncio
async def test_terminal_outcome_after_tools_is_tool_calls_completed() -> None:
    """A run that executed tools before answering is completed/tool_calls_completed."""
    metrics = MetricsCollector()
    provider = _QueuedProvider(
        responses=[
            ProviderResponse(
                content="calling",
                tool_calls=[
                    ToolCall(call_id="tc_1", name="read", arguments={"p": "a"})
                ],
            ),
            ProviderResponse(content="done", tool_calls=[]),
        ]
    )
    loop = AgentLoop(
        provider=provider,
        context_builder=_metrics_builder(),
        tool_executor=_RecorderToolExecutor(),
        metrics=metrics,
    )

    await loop.run(
        user_message="hi",
        system_prompt="sys",
        tools=[
            ToolDefinition(
                name="read",
                description="read",
                parameters={"type": "object", "properties": {}},
            )
        ],
    )

    assert metrics.terminal_outcome_counts() == {"completed:tool_calls_completed": 1}


@pytest.mark.asyncio
async def test_terminal_outcome_records_protocol_anomaly() -> None:
    """A provider tool-protocol anomaly (finish=tool_calls, none parsed) is counted.

    This is the previously-swallowed signal: the adapter sets
    ``tool_protocol_anomaly`` and the loop now records it instead of silently
    treating the turn as a normal completion.
    """
    metrics = MetricsCollector()
    provider = _QueuedProvider(
        responses=[
            ProviderResponse(
                content="I checked it",
                tool_calls=[],
                finish_reason="tool_calls",
                tool_protocol_anomaly="tool_finish_without_parsed_calls",
            )
        ]
    )
    loop = AgentLoop(
        provider=provider, context_builder=_metrics_builder(), metrics=metrics
    )

    result = await loop.run(user_message="hi", system_prompt="sys")

    # Behaviour unchanged: still a "completed" run with the model's text.
    assert result.final_response == "I checked it"
    assert metrics.terminal_outcome_counts() == {"completed:no_tool_calls": 1}
    assert metrics.protocol_anomaly_total() == 1


@pytest.mark.asyncio
async def test_terminal_outcome_max_steps_is_incomplete() -> None:
    """Reaching max_steps records incomplete/max_steps_reached, fallback preserved."""
    metrics = MetricsCollector()
    provider = _QueuedProvider(
        responses=[
            ProviderResponse(
                content="working",
                tool_calls=[
                    ToolCall(call_id="tc_1", name="read", arguments={"p": "a"})
                ],
            ),
            ProviderResponse(
                content="working more",
                tool_calls=[
                    ToolCall(call_id="tc_2", name="read", arguments={"p": "b"})
                ],
            ),
        ]
    )
    loop = AgentLoop(
        provider=provider,
        context_builder=_metrics_builder(),
        tool_executor=_RecorderToolExecutor(),
        config=AgentLoopConfig(max_steps=2),
        metrics=metrics,
    )

    result = await loop.run(
        user_message="hi",
        system_prompt="sys",
        tools=[
            ToolDefinition(
                name="read",
                description="read",
                parameters={"type": "object", "properties": {}},
            )
        ],
    )

    # Regression guard: max_steps still falls back to the last assistant text.
    assert result.final_response == "working more"
    assert metrics.terminal_outcome_counts() == {"incomplete:max_steps_reached": 1}


@pytest.mark.asyncio
async def test_terminal_outcome_provider_error_is_failed() -> None:
    """A non-retryable provider error records failed/provider_error."""
    metrics = MetricsCollector()
    provider = _QueuedProvider(failures=[ProviderAuthError()])
    loop = AgentLoop(
        provider=provider,
        context_builder=_metrics_builder(),
        config=AgentLoopConfig(retry_attempts=0),
        metrics=metrics,
    )

    await loop.run(user_message="hi", system_prompt="sys")

    assert metrics.terminal_outcome_counts() == {"failed:provider_error": 1}


@pytest.mark.asyncio
async def test_terminal_outcome_stop_event_is_cancelled() -> None:
    """A graceful stop records cancelled/cancelled."""
    metrics = MetricsCollector()
    provider = _QueuedProvider(
        responses=[ProviderResponse(content="hello", tool_calls=[])]
    )
    loop = AgentLoop(
        provider=provider, context_builder=_metrics_builder(), metrics=metrics
    )
    stop_event = asyncio.Event()
    stop_event.set()

    async for _event in loop.run_stream(
        user_message="hi", system_prompt="sys", stop_event=stop_event
    ):
        pass

    assert metrics.terminal_outcome_counts() == {"cancelled:cancelled": 1}


@pytest.mark.asyncio
async def test_terminal_outcome_not_recorded_without_metrics() -> None:
    """Without a metrics collector the loop behaves exactly as before."""
    provider = _QueuedProvider(
        responses=[ProviderResponse(content="hello", tool_calls=[])]
    )
    loop = AgentLoop(provider=provider, context_builder=_metrics_builder())

    result = await loop.run(user_message="hi", system_prompt="sys")

    assert result.final_response == "hello"
    assert result.trace_id is None  # no metrics → no trace_id (unchanged)


# ---------------------------------------------------------------------------
# Phase 1 — canonical ledger reconstruction (acceptance criterion)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_canonical_ledger_reconstructs_multi_tool_run() -> None:
    """A multi-tool run must be fully reconstructable from the ledger DB.

    Acceptance for Phase 1: user → assistant → tool_call → tool_result →
    assistant → terminal, with a paired receipt and no orphan results — and
    the loop's user-facing result is unchanged.
    """
    from nahida_bot.db.engine import DatabaseEngine
    from nahida_bot.db.repositories.sqlite_agent_run_repo import SQLiteAgentRunStore

    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    store = SQLiteAgentRunStore(engine)
    try:
        metrics = MetricsCollector()
        provider = _QueuedProvider(
            responses=[
                ProviderResponse(
                    content="calling tool",
                    tool_calls=[
                        ToolCall(
                            call_id="tc_1", name="read_file", arguments={"path": "a"}
                        )
                    ],
                ),
                ProviderResponse(content="done", tool_calls=[]),
            ]
        )
        loop = AgentLoop(
            provider=provider,
            context_builder=_metrics_builder(),
            tool_executor=_RecorderToolExecutor(),
            metrics=metrics,
            run_store=store,
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

        # Behaviour unchanged.
        assert result.final_response == "done"
        assert result.steps == 2
        run_id = result.trace_id
        assert run_id is not None

        # Run finalized as completed.
        run = await store.get_run(run_id)
        assert run is not None
        assert run["terminal_state"] == "completed"

        # Canonical event order reconstructed faithfully.
        events = await store.list_events(run_id)
        assert [e["event_type"] for e in events] == [
            "user_input",
            "assistant_output",
            "tool_call",
            "tool_result",
            "assistant_output",
            "terminal",
        ]
        assert [e["sequence"] for e in events] == [1, 2, 3, 4, 5, 6]

        # One paired receipt, no orphans.
        receipts = await store.list_receipts(run_id)
        assert len(receipts) == 1
        receipt = receipts[0]
        assert receipt["call_id"] == "tc_1"
        assert receipt["tool_name"] == "read_file"
        assert receipt["status"] == "ok"
        assert receipt["verification_status"] == "unverified"
        assert receipt["input_fingerprint"]
        assert "output_hash" in receipt["evidence_json"]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_canonical_ledger_disabled_by_default_is_noop() -> None:
    """Without a run_store the loop uses the null store: no ledger writes, run works."""
    provider = _QueuedProvider(
        responses=[ProviderResponse(content="hello", tool_calls=[])]
    )
    loop = AgentLoop(provider=provider, context_builder=_metrics_builder())

    result = await loop.run(user_message="hi", system_prompt="sys")

    assert result.final_response == "hello"
    # Null store → nothing persisted; run unaffected (Phase 0 behaviour preserved).
