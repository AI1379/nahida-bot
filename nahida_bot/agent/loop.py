"""Agent loop orchestration for provider and tool execution."""

from __future__ import annotations

import asyncio
import json
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import structlog

from nahida_bot.agent.context import ContextBuilder, ContextMessage, ContextPart
from nahida_bot.agent.metrics import MetricsCollector, Trace
from nahida_bot.agent.providers import (
    ChatProvider,
    ProviderError,
    ProviderResponse,
    ToolCall,
    ToolDefinition,
)

logger = structlog.get_logger(__name__)


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
    tool_timeout_seconds: float = 135.0
    tool_retry_attempts: int = 1
    tool_retry_backoff_seconds: float = 0.1
    max_tool_log_chars: int = 400
    tool_use_system_prompt: str = (
        "Tool use policy: When a tool is needed, call it through the structured "
        "tool/function calling interface. Do not merely say that you will call a "
        "tool. After tool results are provided, continue reasoning from the "
        "results and produce the final user-facing answer."
    )
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


@dataclass(slots=True, frozen=True)
class LoopEvent:
    """Streaming event emitted during agent loop execution."""

    type: Literal["text", "tool_start", "tool_end", "done"]
    text: str | None = None
    reasoning: str | None = None
    tool_names: list[str] | None = None
    tool_summary: str | None = None
    final_response: str | None = None
    assistant_messages: list[ContextMessage] | None = None
    tool_messages: list[ContextMessage] | None = None
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
        user_parts: list[ContextPart] | None = None,
        history_messages: list[ContextMessage] | None = None,
        workspace_root: Path | None = None,
        tools: list[ToolDefinition] | None = None,
        provider: ChatProvider | None = None,
        context_builder: ContextBuilder | None = None,
        model: str | None = None,
    ) -> AgentRunResult:
        """Run the agent loop until terminal assistant response is produced.

        Args:
            provider: Override provider for this call only.
            context_builder: Override context builder for this call only.
            model: Override model name for this call only.
        """
        async for event in self.run_stream(
            user_message=user_message,
            system_prompt=system_prompt,
            user_parts=user_parts,
            history_messages=history_messages,
            workspace_root=workspace_root,
            tools=tools,
            provider=provider,
            context_builder=context_builder,
            model=model,
        ):
            if event.type == "done":
                return AgentRunResult(
                    final_response=event.final_response or "",
                    assistant_messages=list(event.assistant_messages or []),
                    tool_messages=list(event.tool_messages or []),
                    steps=event.steps,
                    trace_id=event.trace_id,
                    error=event.error,
                )
        return AgentRunResult(final_response="")

    async def run_stream(
        self,
        *,
        user_message: str,
        system_prompt: str,
        user_parts: list[ContextPart] | None = None,
        history_messages: list[ContextMessage] | None = None,
        workspace_root: Path | None = None,
        tools: list[ToolDefinition] | None = None,
        provider: ChatProvider | None = None,
        context_builder: ContextBuilder | None = None,
        model: str | None = None,
    ) -> AsyncIterator[LoopEvent]:
        """Run the agent loop, yielding :class:`LoopEvent` as progress happens.

        Text events are yielded immediately when the provider produces
        user-visible content — even when tool calls follow in the same turn.
        This lets callers stream progress without waiting for the full loop
        to complete.
        """
        active_provider = provider or self.provider
        active_builder = context_builder or self.context_builder
        trace = self.metrics.new_trace() if self.metrics else None
        provider_default_model = getattr(active_provider, "model", "")
        effective_system_prompt = self._system_prompt_with_tool_guidance(
            system_prompt, tools
        )

        logger.debug(
            "agent_loop.run",
            trace_id=trace.trace_id if trace else "",
            provider_name=getattr(active_provider, "name", ""),
            provider_default_model=provider_default_model,
            model_override=model or "",
            history_count=len(history_messages or []),
            history_roles=[m.role for m in (history_messages or [])[:6]],
            history_sources=[m.source for m in (history_messages or [])[:6]],
            user_preview=user_message[:80],
        )

        conversation = list(history_messages or [])
        conversation.append(
            ContextMessage(
                role="user",
                source="user_input",
                content=user_message,
                parts=list(user_parts or []),
            )
        )
        tool_messages: list[ContextMessage] = []
        assistant_messages: list[ContextMessage] = []

        step = 0
        try:
            for step in range(1, self.config.max_steps + 1):
                prompt_messages = active_builder.build_context(
                    system_prompt=effective_system_prompt,
                    workspace_root=workspace_root,
                    history_messages=conversation,
                )
                logger.debug(
                    "agent_loop.context_built",
                    trace_id=trace.trace_id if trace else "",
                    step=step,
                    message_count=len(prompt_messages),
                    roles=[m.role for m in prompt_messages],
                    sources=[m.source for m in prompt_messages],
                    model_override=model or "",
                )

                response = await self._call_provider_with_retry(
                    messages=prompt_messages,
                    tools=tools,
                    step=step,
                    trace=trace,
                    provider=active_provider,
                    model=model,
                )

                assistant_message = self._build_assistant_message(response)
                if assistant_message is not None:
                    assistant_messages.append(assistant_message)
                    conversation.append(assistant_message)

                display = self._display_content(response)
                reasoning = response.reasoning_content or None
                if display or reasoning:
                    yield LoopEvent(
                        type="text", text=display or None, reasoning=reasoning
                    )

                if not response.tool_calls:
                    self._log_terminal_without_tool_calls(
                        response=response,
                        tools=tools,
                        step=step,
                        trace=trace,
                    )
                    logger.info(
                        "agent_loop.run_completed",
                        trace_id=trace.trace_id if trace else "",
                        reason="no_tool_calls",
                        step=step,
                        max_steps=self.config.max_steps,
                        finish_reason=response.finish_reason or "",
                    )
                    yield LoopEvent(
                        type="done",
                        final_response=display,
                        assistant_messages=list(assistant_messages),
                        tool_messages=list(tool_messages),
                        steps=step,
                        trace_id=trace.trace_id if trace else None,
                    )
                    return

                if self.tool_executor is None:
                    raise RuntimeError(
                        "Provider requested tools but no tool executor is set"
                    )

                yield LoopEvent(
                    type="tool_start",
                    tool_names=[tc.name for tc in response.tool_calls],
                )

                executed_messages = await self._execute_tools(
                    response=response,
                    tools=tools,
                    step=step,
                    trace=trace,
                )
                tool_messages.extend(executed_messages)
                conversation.extend(executed_messages)
                logger.debug(
                    "agent_loop.tools_executed",
                    trace_id=trace.trace_id if trace else "",
                    step=step,
                    tool_call_count=len(response.tool_calls),
                    tool_message_count=len(executed_messages),
                )

                yield LoopEvent(
                    type="tool_end",
                    tool_summary=f"{len(executed_messages)} tool(s) completed",
                )

            final_fallback = (
                assistant_messages[-1].content if assistant_messages else ""
            )
            logger.warning(
                "agent_loop.run_completed",
                trace_id=trace.trace_id if trace else "",
                reason="max_steps_reached",
                step=self.config.max_steps,
                max_steps=self.config.max_steps,
                assistant_message_count=len(assistant_messages),
                tool_message_count=len(tool_messages),
            )
            yield LoopEvent(
                type="done",
                final_response=final_fallback,
                assistant_messages=list(assistant_messages),
                tool_messages=list(tool_messages),
                steps=self.config.max_steps,
                trace_id=trace.trace_id if trace else None,
            )
        except ProviderError as exc:
            logger.warning(
                "agent_loop.provider_error_abort",
                error=str(exc),
                exc_info=True,
            )
            logger.warning(
                "agent_loop.run_completed",
                trace_id=trace.trace_id if trace else "",
                reason="provider_error",
                step=step,
                max_steps=self.config.max_steps,
                error_code=exc.code,
            )
            fallback = assistant_messages[-1].content if assistant_messages else ""
            if not fallback:
                fallback = self.config.provider_error_template.format(code=exc.code)
            yield LoopEvent(
                type="done",
                final_response=fallback,
                assistant_messages=list(assistant_messages),
                tool_messages=list(tool_messages),
                steps=step,
                trace_id=trace.trace_id if trace else None,
                error=exc.code,
            )

    def _system_prompt_with_tool_guidance(
        self,
        system_prompt: str,
        tools: list[ToolDefinition] | None,
    ) -> str:
        if not tools or not self.config.tool_use_system_prompt:
            return system_prompt
        return f"{system_prompt.rstrip()}\n\n{self.config.tool_use_system_prompt}"

    async def _call_provider_with_retry(
        self,
        *,
        messages: list[ContextMessage],
        tools: list[ToolDefinition] | None,
        step: int = 0,
        trace: Trace | None = None,
        provider: ChatProvider | None = None,
        model: str | None = None,
    ) -> ProviderResponse:
        active_provider = provider or self.provider
        attempts = 0
        while True:
            attempts += 1
            t0 = time.monotonic()
            try:
                effective_model = model or getattr(active_provider, "model", "")
                logger.debug(
                    "agent_loop.provider_call_start",
                    trace_id=trace.trace_id if trace else "",
                    provider_name=getattr(active_provider, "name", ""),
                    provider_api_family=getattr(active_provider, "api_family", ""),
                    provider_default_model=getattr(active_provider, "model", ""),
                    requested_model=model or "",
                    effective_model=effective_model,
                    step=step,
                    attempt=attempts,
                    message_count=len(messages),
                    tool_count=len(tools or []),
                    roles=[m.role for m in messages],
                    sources=[m.source for m in messages],
                )
                response = await active_provider.chat(
                    messages=messages,
                    tools=tools,
                    timeout_seconds=self.config.provider_timeout_seconds,
                    model=model,
                )
                logger.debug(
                    "agent_loop.provider_call_done",
                    trace_id=trace.trace_id if trace else "",
                    provider_name=getattr(active_provider, "name", ""),
                    effective_model=effective_model,
                    step=step,
                    attempt=attempts,
                    latency_seconds=round(time.monotonic() - t0, 3),
                    finish_reason=response.finish_reason or "",
                    tool_call_count=len(response.tool_calls),
                    content_chars=len(response.content or ""),
                    response_extra_keys=sorted(response.extra.keys()),
                    raw_response_summary=self._raw_response_summary(
                        response.raw_response
                    ),
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
                logger.warning(
                    "agent_loop.provider_call_failed",
                    trace_id=trace.trace_id if trace else "",
                    provider_name=getattr(active_provider, "name", ""),
                    requested_model=model or "",
                    effective_model=model or getattr(active_provider, "model", ""),
                    step=step,
                    attempt=attempts,
                    error_code=exc.code,
                    retryable=exc.retryable,
                    will_retry=can_retry,
                )
                if not can_retry:
                    raise
                await asyncio.sleep(self.config.retry_backoff_seconds)

    def _log_terminal_without_tool_calls(
        self,
        *,
        response: ProviderResponse,
        tools: list[ToolDefinition] | None,
        step: int,
        trace: Trace | None,
    ) -> None:
        # TODO: This function is just used for debugging and should be removed
        # once we have more confidence in the tool calling signals from providers.
        content = self._display_content(response)
        tool_names = [tool.name for tool in tools or []]
        lowered = content.lower()
        looks_like_tool_promise = (
            "tool" in lowered
            or "工具" in content
            or "调用" in content
            or "我去" in content
            or "我来" in content
            or "让我" in content
            or "看一下" in content
            or "查一下" in content
            or "搜索" in content
            or "读取" in content
            or "检查" in content
            or "执行" in content
            or "运行" in content
            or "i will" in lowered
            or "i'll" in lowered
            or "let me" in lowered
            or "going to" in lowered
            or "check" in lowered
            or "search" in lowered
            or "look up" in lowered
            or "read " in lowered
            or "run " in lowered
            or any(name.lower() in lowered for name in tool_names)
        )
        finish_reason = response.finish_reason or ""
        finish_implies_tools = finish_reason in {"tool_calls", "tool_use"}
        log = (
            logger.warning
            if looks_like_tool_promise or finish_implies_tools
            else logger.debug
        )
        log(
            "agent_loop.terminal_without_tool_calls",
            trace_id=trace.trace_id if trace else "",
            step=step,
            finish_reason=finish_reason,
            content_preview=content[:200],
            available_tools=tool_names[:20],
            available_tool_count=len(tool_names),
            looks_like_tool_promise=looks_like_tool_promise,
            finish_implies_tools=finish_implies_tools,
            response_extra_keys=sorted(response.extra.keys()),
            raw_response_summary=self._raw_response_summary(response.raw_response),
        )

    def _build_assistant_message(
        self,
        response: ProviderResponse,
    ) -> ContextMessage | None:
        display_content = self._display_content(response)
        has_hidden_output = any(
            response.extra.get(key) is not None
            for key in (
                "response_id",
                "response_output",
                "generated_images",
                "builtin_tool_calls",
            )
        )
        if not display_content and not response.tool_calls and not has_hidden_output:
            return None

        metadata: dict[str, object] = {}
        if response.finish_reason is not None:
            metadata["finish_reason"] = response.finish_reason
        for key in (
            "response_id",
            "response_output",
            "generated_images",
            "builtin_tool_calls",
        ):
            value = response.extra.get(key)
            if value is not None:
                metadata[key] = value
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
            content=display_content,
            metadata=metadata or None,
            reasoning=response.reasoning_content,
            reasoning_signature=response.reasoning_signature,
            has_redacted_thinking=response.has_redacted_thinking,
        )

    def _display_content(self, response: ProviderResponse) -> str:
        if response.content:
            return response.content

        generated = response.extra.get("generated_images")
        if isinstance(generated, list) and generated:
            return "[generated image available]"

        builtin_calls = response.extra.get("builtin_tool_calls")
        if isinstance(builtin_calls, list) and builtin_calls:
            return "[built-in tool output available]"

        if response.reasoning_content:
            return ""

        if response.refusal:
            return response.refusal

        response_shape = response.extra.get("response_shape")
        if isinstance(response_shape, dict):
            output_types = response_shape.get("output_types")
            if isinstance(output_types, list) and output_types:
                return "[empty response output received]"

        return ""

    def _raw_response_summary(
        self,
        raw_response: dict[str, object] | None,
    ) -> dict[str, object]:
        """Return a compact provider-native shape for diagnosing stop decisions."""
        if raw_response is None:
            return {}

        summary: dict[str, object] = {
            "keys": sorted(raw_response.keys())[:20],
        }

        choices = raw_response.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                summary["finish_reason"] = first.get("finish_reason")
                if isinstance(message, dict):
                    tool_calls = message.get("tool_calls")
                    summary["message_keys"] = sorted(message.keys())[:20]
                    summary["has_message_tool_calls"] = isinstance(tool_calls, list)
                    summary["message_tool_call_count"] = (
                        len(tool_calls) if isinstance(tool_calls, list) else 0
                    )

        output = raw_response.get("output")
        if isinstance(output, list):
            output_types: list[str] = []
            function_call_count = 0
            for item in output:
                if not isinstance(item, dict):
                    output_types.append(type(item).__name__)
                    continue
                item_type = item.get("type")
                output_types.append(
                    item_type if isinstance(item_type, str) else "<missing>"
                )
                if item_type == "function_call":
                    function_call_count += 1
            summary["status"] = raw_response.get("status")
            summary["output_types"] = output_types[:20]
            summary["function_call_count"] = function_call_count

        content = raw_response.get("content")
        if isinstance(content, list):
            block_types: list[str] = []
            tool_use_count = 0
            for block in content:
                if not isinstance(block, dict):
                    block_types.append(type(block).__name__)
                    continue
                block_type = block.get("type")
                block_types.append(
                    block_type if isinstance(block_type, str) else "<missing>"
                )
                if block_type == "tool_use":
                    tool_use_count += 1
            summary["stop_reason"] = raw_response.get("stop_reason")
            summary["content_block_types"] = block_types[:20]
            summary["tool_use_count"] = tool_use_count

        return summary

    async def _execute_tools(
        self,
        *,
        response: ProviderResponse,
        tools: list[ToolDefinition] | None,
        step: int = 0,
        trace: Trace | None = None,
    ) -> list[ContextMessage]:
        messages: list[ContextMessage] = []

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
            logger.debug(
                "agent_loop.tool_call_start",
                trace_id=trace.trace_id if trace else "",
                step=step,
                tool_name=tool_call.name,
                tool_call_id=tool_call.call_id,
                attempt=attempt,
                max_attempts=max_attempts,
                timeout_seconds=self.config.tool_timeout_seconds,
            )
            try:
                raw_result = await asyncio.wait_for(
                    self.tool_executor.execute(tool_call),
                    timeout=self.config.tool_timeout_seconds,
                )
                result = self._coerce_tool_result(raw_result)
            except TimeoutError:
                result = ToolExecutionResult.error(
                    code="tool_timeout",
                    message=(
                        "Tool execution timed out after "
                        f"{self.config.tool_timeout_seconds:.1f}s"
                    ),
                    retryable=False,
                )
                logger.warning(
                    "agent_loop.tool_call_timeout",
                    trace_id=trace.trace_id if trace else "",
                    step=step,
                    tool_name=tool_call.name,
                    tool_call_id=tool_call.call_id,
                    attempt=attempt,
                    timeout_seconds=self.config.tool_timeout_seconds,
                )
            except Exception as exc:
                result = ToolExecutionResult.error(
                    code="tool_execution_exception",
                    message=f"Tool execution raised: {type(exc).__name__}",
                    retryable=False,
                    logs=[str(exc)],
                )
                logger.warning(
                    "agent_loop.tool_call_exception",
                    trace_id=trace.trace_id if trace else "",
                    step=step,
                    tool_name=tool_call.name,
                    tool_call_id=tool_call.call_id,
                    attempt=attempt,
                    error_type=type(exc).__name__,
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
            logger.debug(
                "agent_loop.tool_call_done",
                trace_id=trace.trace_id if trace else "",
                step=step,
                tool_name=tool_call.name,
                tool_call_id=tool_call.call_id,
                attempt=attempt,
                phase=phase,
                success=not result.is_error,
                error_code=result.error_code or "",
                retryable=result.retryable,
                latency_seconds=round(time.monotonic() - t0, 3),
            )
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
