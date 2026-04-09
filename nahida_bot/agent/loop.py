"""Agent loop orchestration for provider and tool execution."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from nahida_bot.agent.context import ContextBuilder, ContextMessage
from nahida_bot.agent.metrics import MetricsCollector, Trace
from nahida_bot.agent.providers import (
    ChatProvider,
    ProviderError,
    ProviderResponse,
    ToolCall,
    ToolDefinition,
)

logger = logging.getLogger(__name__)


class ToolExecutor(ABC):
    """Executor contract for tool calls emitted by providers."""

    @abstractmethod
    async def execute(self, tool_call: ToolCall) -> "ToolExecutionResult":
        """Execute a tool call and return structured result."""
        raise NotImplementedError


@dataclass(slots=True, frozen=True)
class ToolExecutionResult:
    """Structured tool execution result injected back to model context."""

    output: object | None = None
    logs: list[str] = field(default_factory=list)
    is_error: bool = False
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False

    @classmethod
    def success(
        cls,
        output: object | None,
        *,
        logs: list[str] | None = None,
    ) -> "ToolExecutionResult":
        """Create a successful structured tool result."""
        return cls(output=output, logs=list(logs or []), is_error=False)

    @classmethod
    def error(
        cls,
        *,
        code: str,
        message: str,
        retryable: bool,
        logs: list[str] | None = None,
    ) -> "ToolExecutionResult":
        """Create an explainable tool error result."""
        return cls(
            output=None,
            logs=list(logs or []),
            is_error=True,
            error_code=code,
            error_message=message,
            retryable=retryable,
        )


@dataclass(slots=True, frozen=True)
class AgentLoopConfig:
    """Config for loop retries and termination conditions."""

    max_steps: int = 8
    provider_timeout_seconds: float = 30.0
    retry_attempts: int = 2
    retry_backoff_seconds: float = 0.2
    tool_retry_attempts: int = 1
    tool_retry_backoff_seconds: float = 0.1
    max_tool_log_chars: int = 400
    provider_error_template: str = (
        "Service temporarily unavailable ({code}). Please try again later."
    )


@dataclass(slots=True, frozen=True)
class AgentRunResult:
    """Result from an agent loop execution."""

    final_response: str
    assistant_messages: list[ContextMessage] = field(default_factory=list)
    tool_messages: list[ContextMessage] = field(default_factory=list)
    steps: int = 0
    trace_id: str | None = None
    error: str | None = None


class AgentLoop:
    """Minimal agent loop with provider calls, tools, and stop conditions."""

    def __init__(
        self,
        *,
        provider: ChatProvider,
        context_builder: ContextBuilder,
        config: AgentLoopConfig | None = None,
        tool_executor: ToolExecutor | None = None,
        metrics: MetricsCollector | None = None,
    ) -> None:
        self.provider = provider
        self.context_builder = context_builder
        self.config = config or AgentLoopConfig()
        self.tool_executor = tool_executor
        self.metrics = metrics

    async def run(
        self,
        *,
        user_message: str,
        system_prompt: str,
        history_messages: list[ContextMessage] | None = None,
        workspace_root=None,
        tools: list[ToolDefinition] | None = None,
    ) -> AgentRunResult:
        """Run the agent loop until terminal assistant response is produced."""
        trace = self.metrics.new_trace() if self.metrics else None
        conversation = list(history_messages or [])
        conversation.append(
            ContextMessage(role="user", source="user_input", content=user_message)
        )
        tool_messages: list[ContextMessage] = []
        assistant_messages: list[ContextMessage] = []

        step = 0
        try:
            for step in range(1, self.config.max_steps + 1):
                prompt_messages = self.context_builder.build_context(
                    system_prompt=system_prompt,
                    workspace_root=workspace_root,
                    history_messages=conversation,
                    tool_messages=tool_messages,
                )

                response = await self._call_provider_with_retry(
                    messages=prompt_messages,
                    tools=tools,
                    step=step,
                    trace=trace,
                )

                assistant_message = self._build_assistant_message(response)
                if assistant_message is not None:
                    assistant_messages.append(assistant_message)
                    conversation.append(assistant_message)

                if not response.tool_calls:
                    return AgentRunResult(
                        final_response=response.content or "",
                        assistant_messages=assistant_messages,
                        tool_messages=tool_messages,
                        steps=step,
                        trace_id=trace.trace_id if trace else None,
                    )

                if self.tool_executor is None:
                    raise RuntimeError(
                        "Provider requested tools but no tool executor is set"
                    )

                executed_messages = await self._execute_tools(
                    response=response,
                    tools=tools,
                    step=step,
                    trace=trace,
                )
                tool_messages.extend(executed_messages)
                conversation.extend(executed_messages)

            final_fallback = (
                assistant_messages[-1].content if assistant_messages else ""
            )
            return AgentRunResult(
                final_response=final_fallback,
                assistant_messages=assistant_messages,
                tool_messages=tool_messages,
                steps=self.config.max_steps,
                trace_id=trace.trace_id if trace else None,
            )
        except ProviderError as exc:
            logger.warning(
                "Agent loop aborted by provider error: %s", exc, exc_info=True
            )
            fallback = assistant_messages[-1].content if assistant_messages else ""
            if not fallback:
                fallback = self.config.provider_error_template.format(code=exc.code)
            return AgentRunResult(
                final_response=fallback,
                assistant_messages=assistant_messages,
                tool_messages=tool_messages,
                steps=step,
                trace_id=trace.trace_id if trace else None,
                error=exc.code,
            )

    async def _call_provider_with_retry(
        self,
        *,
        messages: list[ContextMessage],
        tools: list[ToolDefinition] | None,
        step: int = 0,
        trace: Trace | None = None,
    ) -> ProviderResponse:
        attempts = 0
        while True:
            attempts += 1
            t0 = time.monotonic()
            try:
                response = await self.provider.chat(
                    messages=messages,
                    tools=tools,
                    timeout_seconds=self.config.provider_timeout_seconds,
                )
                if trace is not None and self.metrics is not None:
                    self.metrics.record_provider_call(
                        trace, step=step, latency_seconds=time.monotonic() - t0
                    )
                return response
            except ProviderError as exc:
                if trace is not None and self.metrics is not None:
                    self.metrics.record_provider_call(
                        trace,
                        step=step,
                        latency_seconds=time.monotonic() - t0,
                        error_code=exc.code,
                        retryable=exc.retryable,
                    )
                can_retry = exc.retryable and attempts <= self.config.retry_attempts
                if not can_retry:
                    raise
                await asyncio.sleep(self.config.retry_backoff_seconds)

    def _build_assistant_message(
        self,
        response: ProviderResponse,
    ) -> ContextMessage | None:
        if response.content is None and not response.tool_calls:
            return None

        metadata: dict[str, object] = {}
        if response.finish_reason is not None:
            metadata["finish_reason"] = response.finish_reason
        if response.tool_calls:
            metadata["tool_calls"] = [
                {
                    "id": tool_call.call_id,
                    "name": tool_call.name,
                    "arguments": tool_call.arguments,
                }
                for tool_call in response.tool_calls
            ]

        return ContextMessage(
            role="assistant",
            source="provider_response",
            content=response.content or "",
            metadata=metadata or None,
        )

    async def _execute_tools(
        self,
        *,
        response: ProviderResponse,
        tools: list[ToolDefinition] | None,
        step: int = 0,
        trace: Trace | None = None,
    ) -> list[ContextMessage]:
        messages: list[ContextMessage] = []
        assert self.tool_executor is not None

        definitions = self._index_tools(tools)
        for tool_call in response.tool_calls:
            validation_error = self._validate_tool_call(
                tool_call=tool_call,
                definitions=definitions,
            )
            if validation_error is not None:
                messages.append(
                    self._build_tool_message(
                        tool_call=tool_call,
                        phase="prepare_failed",
                        attempt=0,
                        result=validation_error,
                    )
                )
                continue

            result, attempt, phase = await self._execute_tool_with_lifecycle(
                tool_call, step=step, trace=trace
            )
            messages.append(
                self._build_tool_message(
                    tool_call=tool_call,
                    phase=phase,
                    attempt=attempt,
                    result=result,
                )
            )
        return messages

    async def _execute_tool_with_lifecycle(
        self,
        tool_call: ToolCall,
        *,
        step: int = 0,
        trace: Trace | None = None,
    ) -> tuple[ToolExecutionResult, int, str]:
        if self.tool_executor is None:
            raise RuntimeError("Tool executor is not set")
        max_attempts = max(1, self.config.tool_retry_attempts + 1)

        for attempt in range(1, max_attempts + 1):
            t0 = time.monotonic()
            try:
                raw_result = await self.tool_executor.execute(tool_call)
                result = self._coerce_tool_result(raw_result)
            except Exception as exc:
                result = ToolExecutionResult.error(
                    code="tool_execution_exception",
                    message=f"Tool execution raised: {type(exc).__name__}",
                    retryable=False,
                    logs=[str(exc)],
                )

            if trace is not None and self.metrics is not None:
                self.metrics.record_tool_call(
                    trace,
                    step=step,
                    tool_name=tool_call.name,
                    latency_seconds=time.monotonic() - t0,
                    success=not result.is_error,
                    error_code=result.error_code,
                    retryable=result.retryable,
                )

            if result.is_error and result.retryable and attempt < max_attempts:
                await asyncio.sleep(self.config.tool_retry_backoff_seconds)
                continue

            phase = "failed" if result.is_error else "completed"
            return result, attempt, phase

        return (
            ToolExecutionResult.error(
                code="tool_execution_unknown",
                message="Tool execution finished without result",
                retryable=False,
            ),
            max_attempts,
            "failed",
        )

    def _coerce_tool_result(self, result: Any) -> ToolExecutionResult:
        if isinstance(result, ToolExecutionResult):
            return result

        # Backward compatibility: allow simple string-returning executors.
        return ToolExecutionResult.success(output=result)

    def _index_tools(
        self,
        tools: list[ToolDefinition] | None,
    ) -> dict[str, ToolDefinition]:
        if not tools:
            return {}
        return {tool.name: tool for tool in tools}

    def _validate_tool_call(
        self,
        *,
        tool_call: ToolCall,
        definitions: dict[str, ToolDefinition],
    ) -> ToolExecutionResult | None:
        definition = definitions.get(tool_call.name)
        if definition is None:
            return ToolExecutionResult.error(
                code="tool_not_registered",
                message=f"Tool '{tool_call.name}' is not registered",
                retryable=False,
            )

        schema = definition.parameters
        if not isinstance(schema, dict):
            return ToolExecutionResult.error(
                code="tool_schema_invalid",
                message=f"Tool '{tool_call.name}' schema must be an object",
                retryable=False,
            )

        schema_type = schema.get("type")
        if schema_type != "object":
            return ToolExecutionResult.error(
                code="tool_schema_invalid",
                message=f"Tool '{tool_call.name}' schema type must be object",
                retryable=False,
            )

        properties_raw = schema.get("properties", {})
        if not isinstance(properties_raw, dict):
            return ToolExecutionResult.error(
                code="tool_schema_invalid",
                message=f"Tool '{tool_call.name}' properties must be an object",
                retryable=False,
            )

        required_raw = schema.get("required", [])
        if not isinstance(required_raw, list) or not all(
            isinstance(item, str) for item in required_raw
        ):
            return ToolExecutionResult.error(
                code="tool_schema_invalid",
                message=f"Tool '{tool_call.name}' required must be a string array",
                retryable=False,
            )

        required_fields = set(required_raw)
        missing = [
            field for field in required_fields if field not in tool_call.arguments
        ]
        if missing:
            return ToolExecutionResult.error(
                code="tool_arguments_invalid",
                message=f"Tool '{tool_call.name}' missing required arguments: {', '.join(sorted(missing))}",
                retryable=False,
            )

        additional = schema.get("additionalProperties", True)
        if additional is False:
            extra_fields = [
                key for key in tool_call.arguments if key not in properties_raw
            ]
            if extra_fields:
                return ToolExecutionResult.error(
                    code="tool_arguments_invalid",
                    message=(
                        f"Tool '{tool_call.name}' has unsupported arguments: "
                        f"{', '.join(sorted(extra_fields))}"
                    ),
                    retryable=False,
                )

        for key, value in tool_call.arguments.items():
            property_schema = properties_raw.get(key)
            if not isinstance(property_schema, dict):
                continue

            expected_type = property_schema.get("type")
            if not isinstance(expected_type, str):
                continue

            if not self._matches_json_type(expected_type, value):
                return ToolExecutionResult.error(
                    code="tool_arguments_invalid",
                    message=(
                        f"Tool '{tool_call.name}' argument '{key}' type mismatch: "
                        f"expected {expected_type}"
                    ),
                    retryable=False,
                )

        return None

    def _matches_json_type(self, expected_type: str, value: object) -> bool:
        if expected_type == "string":
            return isinstance(value, str)
        if expected_type == "number":
            return isinstance(value, int | float) and not isinstance(value, bool)
        if expected_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected_type == "boolean":
            return isinstance(value, bool)
        if expected_type == "object":
            return isinstance(value, dict)
        if expected_type == "array":
            return isinstance(value, list)
        if expected_type == "null":
            return value is None
        return True

    def _build_tool_message(
        self,
        *,
        tool_call: ToolCall,
        phase: str,
        attempt: int,
        result: ToolExecutionResult,
    ) -> ContextMessage:
        payload = {
            "status": "error" if result.is_error else "ok",
            "output": result.output,
            "error": {
                "code": result.error_code,
                "message": result.error_message,
                "retryable": result.retryable,
            }
            if result.is_error
            else None,
            "logs": self._trim_logs(result.logs),
        }

        content = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return ContextMessage(
            role="tool",
            source=f"tool_result:{tool_call.name}",
            content=content,
            metadata={
                "tool_call_id": tool_call.call_id,
                "tool_name": tool_call.name,
                "lifecycle": {
                    "phase": phase,
                    "attempt": attempt,
                },
            },
        )

    def _trim_logs(self, logs: list[str]) -> list[str]:
        if not logs:
            return []

        budget = max(0, self.config.max_tool_log_chars)
        if budget == 0:
            return []

        trimmed: list[str] = []
        used = 0
        for line in logs:
            remaining = budget - used
            if remaining <= 0:
                break

            snippet = line[:remaining]
            trimmed.append(snippet)
            used += len(snippet)

        return trimmed
