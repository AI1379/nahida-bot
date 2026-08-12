"""Agent loop orchestration for provider and tool execution."""

from __future__ import annotations

import asyncio
import json
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

import structlog

from nahida_bot.agent.context import ContextBuilder, ContextMessage, ContextPart
from nahida_bot.agent.metrics import MetricsCollector, Trace
from nahida_bot.agent.runtime import (
    AgentRunContext,
    AgentRunStore,
    NullAgentRunStore,
    RunRecorder,
)
from nahida_bot.core.config import AgentConfig
from nahida_bot.agent.providers import (
    ChatProvider,
    ProviderError,
    ProviderResponse,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)

if TYPE_CHECKING:
    # AuthorizationGate is referenced only in annotations + duck-typed at
    # runtime (self.authorization.authorize). Imported under TYPE_CHECKING to
    # avoid a top-level loop → identity → plugins → loop circular import.
    from nahida_bot.identity.authorization import AuthorizationGate

logger = structlog.get_logger(__name__)


class _StopRequested(Exception):
    """Internal sentinel: stop was requested mid provider-call.

    Raised by ``_call_provider_with_retry`` when ``stop_event`` fires during an
    in-flight ``provider.chat()`` (the call is cancelled so httpx aborts the
    request). Caught in ``run_stream`` to emit the cancelled ``done`` event with
    the partial turn. Deliberately NOT a ``ProviderError`` subclass so it
    bypasses the retry path.
    """


class ToolExecutor(ABC):
    """Executor contract for tool calls emitted by providers."""

    @abstractmethod
    async def execute(self, tool_call: ToolCall) -> "ToolExecutionResult":
        """Execute a tool call and return structured result."""
        raise NotImplementedError

    def tool_requires_admin(self, tool_name: str) -> bool:
        """Return whether a registered tool requires an administrator sender."""
        return False


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


# The runtime loop config IS the pydantic ``AgentConfig`` from core.config.
# Kept under this name for the public ``nahida_bot.agent`` re-export and
# existing call sites; new fields only need to be added to ``AgentConfig``.
# (Resolves the long-standing duplication noted in subtraction-backlog 2.4.)
AgentLoopConfig = AgentConfig


@dataclass(slots=True, frozen=True)
class AgentRunResult:
    """Result from an agent loop execution."""

    final_response: str
    assistant_messages: list[ContextMessage] = field(default_factory=list)
    tool_messages: list[ContextMessage] = field(default_factory=list)
    ordered_transcript: list[ContextMessage] = field(default_factory=list)
    steps: int = 0
    trace_id: str | None = None
    error: str | None = None
    total_usage: TokenUsage | None = None
    # Trusted terminal state propagated from the loop's ``done`` event
    # (issue #42). One of ``completed`` / ``incomplete`` / ``failed`` /
    # ``cancelled``. Empty only for the legacy empty-fallback path that never
    # emitted a real ``done`` event; callers must treat an empty value as
    # ``unverified`` rather than success.
    terminal_state: str = ""
    terminal_reason: str = ""

    @property
    def is_terminal_success(self) -> bool:
        """True only when the loop reports a clean ``completed`` terminal state."""
        return self.terminal_state == "completed"

    @classmethod
    def from_done_event(cls, event: LoopEvent) -> AgentRunResult:
        """Reconstruct a result from a ``done`` :class:`LoopEvent`.

        Centralized so the ``LoopEvent`` → ``AgentRunResult`` mapping is in one
        place — adding a field to either type can't silently drop it the way
        the field-by-field copies did (the Phase 5 ``ordered_transcript``
        omission that left replay dormant in production).
        """
        return cls(
            final_response=event.final_response or "",
            assistant_messages=list(event.assistant_messages or []),
            tool_messages=list(event.tool_messages or []),
            ordered_transcript=list(event.ordered_transcript or []),
            steps=event.steps,
            trace_id=event.trace_id,
            error=event.error,
            total_usage=event.total_usage,
            terminal_state=event.terminal_state or "",
            terminal_reason=event.terminal_reason or "",
        )


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
    ordered_transcript: list[ContextMessage] | None = None
    steps: int = 0
    trace_id: str | None = None
    error: str | None = None
    total_usage: TokenUsage | None = None
    # Trusted terminal state/reason for ``done`` events (issue #42). Empty for
    # non-``done`` events. Consumers must treat an empty ``terminal_state`` on
    # a ``done`` event as ``unverified`` rather than as success.
    terminal_state: str = ""
    terminal_reason: str = ""


@dataclass(slots=True, frozen=True)
class _LoopRequest:
    """Normalized inputs for one streaming agent loop run."""

    user_message: str
    system_prompt: str
    user_parts: list[ContextPart] | None
    history_messages: list[ContextMessage] | None
    workspace_root: Path | None
    tools: list[ToolDefinition] | None
    provider: ChatProvider | None
    context_builder: ContextBuilder | None
    model: str | None
    stop_event: asyncio.Event | None
    session_id: str | None
    workspace_id: str | None
    provider_id: str | None
    origin: str
    sender_account_key: str


@dataclass(slots=True)
class _LoopRuntime:
    """Mutable state shared by loop steps and terminal handlers."""

    request: _LoopRequest
    provider: ChatProvider
    context_builder: ContextBuilder
    trace: Trace | None
    recorder: RunRecorder
    effective_system_prompt: str
    history: list[ContextMessage]
    active_turn_messages: list[ContextMessage]
    tool_messages: list[ContextMessage] = field(default_factory=list)
    assistant_messages: list[ContextMessage] = field(default_factory=list)
    total_usage: TokenUsage = field(default_factory=TokenUsage)
    step: int = 0


@dataclass(slots=True, frozen=True)
class _StepResponse:
    """Provider response plus its user-visible projections."""

    response: ProviderResponse
    display: str
    reasoning: str | None


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
        run_store: AgentRunStore | None = None,
        authorization: AuthorizationGate | None = None,
    ) -> None:
        self.provider = provider
        self.context_builder = context_builder
        self.config = config or AgentConfig()
        self.tool_executor = tool_executor
        self.metrics = metrics
        # Canonical run ledger (Phase 1). Defaults to a no-op store so the loop
        # can drive the recorder unconditionally; app.py passes a real store
        # when agent_runtime.canonical_ledger_enabled is true.
        self.run_store: AgentRunStore = run_store or NullAgentRunStore()
        # Phase A action-authorization gate. None ⇒ no gating (legacy). When
        # present, privileged tools (exec/message/workspace_write) require the
        # run's sender_account_key to be a declared admin. Decoupled from memory.
        self.authorization = authorization

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
        stop_event: asyncio.Event | None = None,
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
            stop_event=stop_event,
        ):
            if event.type == "done":
                return AgentRunResult.from_done_event(event)
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
        stop_event: asyncio.Event | None = None,
        session_id: str | None = None,
        workspace_id: str | None = None,
        provider_id: str | None = None,
        origin: str = "",
        sender_account_key: str = "",
    ) -> AsyncIterator[LoopEvent]:
        """Run the agent loop, yielding :class:`LoopEvent` as progress happens.

        Text events are yielded immediately when the provider produces
        user-visible content — even when tool calls follow in the same turn.
        This lets callers stream progress without waiting for the full loop
        to complete.
        """
        request = _LoopRequest(
            user_message=user_message,
            system_prompt=system_prompt,
            user_parts=user_parts,
            history_messages=history_messages,
            workspace_root=workspace_root,
            tools=tools,
            provider=provider,
            context_builder=context_builder,
            model=model,
            stop_event=stop_event,
            session_id=session_id,
            workspace_id=workspace_id,
            provider_id=provider_id,
            origin=origin,
            sender_account_key=sender_account_key,
        )
        runtime = await self._start_loop_runtime(request)
        try:
            for step in range(1, self.config.max_steps + 1):
                runtime.step = step
                if request.stop_event is not None and request.stop_event.is_set():
                    yield await self._cancel_runtime(
                        runtime,
                        steps=max(step - 1, 0),
                        final_response=self._latest_assistant_content(runtime),
                    )
                    return

                outcome = await self._call_loop_step(runtime)
                if outcome.display or outcome.reasoning:
                    yield LoopEvent(
                        type="text",
                        text=outcome.display or None,
                        reasoning=outcome.reasoning,
                    )
                await self._record_step_response(runtime, outcome)
                response = outcome.response

                if not response.tool_calls:
                    yield await self._complete_without_tools(
                        runtime,
                        response=response,
                        display=outcome.display,
                    )
                    return

                if request.stop_event is not None and request.stop_event.is_set():
                    yield await self._cancel_runtime(
                        runtime,
                        steps=step,
                        final_response=outcome.display,
                    )
                    return

                if self.tool_executor is None:
                    logger.error(
                        "agent_loop.tool_executor_missing",
                        trace_id=runtime.trace.trace_id if runtime.trace else "",
                        step=step,
                        requested_tool_count=len(response.tool_calls),
                        requested_tool_names=[tc.name for tc in response.tool_calls],
                    )
                    raise RuntimeError(
                        "Provider requested tools but no tool executor is set"
                    )

                yield LoopEvent(
                    type="tool_start",
                    tool_names=[tc.name for tc in response.tool_calls],
                )

                executed_messages = await self._execute_tools(
                    response=response,
                    tools=request.tools,
                    step=step,
                    trace=runtime.trace,
                    recorder=runtime.recorder,
                    sender_account_key=request.sender_account_key,
                )
                runtime.tool_messages.extend(executed_messages)
                runtime.active_turn_messages.extend(executed_messages)
                logger.debug(
                    "agent_loop.tools_executed",
                    trace_id=runtime.trace.trace_id if runtime.trace else "",
                    step=step,
                    tool_call_count=len(response.tool_calls),
                    tool_message_count=len(executed_messages),
                    tool_message_sources=[m.source for m in executed_messages],
                )

                yield LoopEvent(
                    type="tool_end",
                    tool_summary=f"{len(executed_messages)} tool(s) completed",
                )

            yield await self._max_steps_event(runtime)
        except _StopRequested:
            yield await self._stop_during_provider_event(runtime)
        except ProviderError as exc:
            yield await self._provider_error_event(runtime, exc)
        finally:
            # Guarantee the ledger run is finalized even if an unexpected
            # exception escapes the known exit paths. A no-op when an exit
            # path already recorded a terminal event.
            await runtime.recorder.ensure_finalized()

    async def _call_loop_step(self, runtime: _LoopRuntime) -> _StepResponse:
        """Build one prompt, call the provider, and update in-memory state."""
        request = runtime.request
        prompt_messages = runtime.context_builder.build_context(
            system_prompt=runtime.effective_system_prompt,
            workspace_root=request.workspace_root,
            history_messages=runtime.history,
            protected_messages=runtime.active_turn_messages,
        )
        logger.debug(
            "agent_loop.context_built",
            trace_id=runtime.trace.trace_id if runtime.trace else "",
            step=runtime.step,
            message_count=len(prompt_messages),
            roles=[message.role for message in prompt_messages],
            sources=[message.source for message in prompt_messages],
            context_summary=self._message_summary(prompt_messages),
            model_override=request.model or "",
        )
        response = await self._call_provider_with_retry(
            messages=prompt_messages,
            tools=request.tools,
            step=runtime.step,
            trace=runtime.trace,
            provider=runtime.provider,
            model=request.model,
            stop_event=request.stop_event,
        )
        if response.usage is not None:
            runtime.total_usage = self._merge_usage(
                runtime.total_usage,
                response.usage,
            )
        usage = runtime.total_usage
        logger.debug(
            "agent_loop.step_response",
            trace_id=runtime.trace.trace_id if runtime.trace else "",
            step=runtime.step,
            response_summary=self._provider_response_summary(response),
            total_input_tokens=usage.input_tokens,
            total_output_tokens=usage.output_tokens,
            total_cached_tokens=usage.cached_tokens,
            total_reasoning_tokens=usage.reasoning_tokens,
        )
        assistant_message = self._build_assistant_message(response)
        if assistant_message is not None:
            runtime.assistant_messages.append(assistant_message)
            runtime.active_turn_messages.append(assistant_message)
        return _StepResponse(
            response=response,
            display=self._display_content(response),
            reasoning=response.reasoning_content or None,
        )

    @staticmethod
    async def _record_step_response(
        runtime: _LoopRuntime,
        outcome: _StepResponse,
    ) -> None:
        response = outcome.response
        await runtime.recorder.assistant_output(
            step=runtime.step,
            content=outcome.display,
            finish_reason=response.finish_reason or "",
            tool_call_count=len(response.tool_calls),
            protocol_anomaly=response.tool_protocol_anomaly or "",
        )

    @staticmethod
    def _merge_usage(current: TokenUsage, update: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=current.input_tokens + update.input_tokens,
            output_tokens=current.output_tokens + update.output_tokens,
            cached_tokens=current.cached_tokens + update.cached_tokens,
            reasoning_tokens=current.reasoning_tokens + update.reasoning_tokens,
            cache_creation_tokens=(
                current.cache_creation_tokens + update.cache_creation_tokens
            ),
        )

    async def _start_loop_runtime(self, request: _LoopRequest) -> _LoopRuntime:
        """Resolve run-scoped dependencies, start the ledger, and seed messages."""
        active_provider = request.provider or self.provider
        active_builder = request.context_builder or self.context_builder
        trace = self.metrics.new_trace() if self.metrics else None
        provider_default_model = getattr(active_provider, "model", "")
        effective_system_prompt = self._system_prompt_with_tool_guidance(
            request.system_prompt,
            request.tools,
        )
        run_context = AgentRunContext(
            run_id=trace.trace_id if trace else uuid4().hex,
            trace_id=trace.trace_id if trace else "",
            session_id=request.session_id,
            workspace_id=request.workspace_id,
            provider_id=request.provider_id,
        )
        recorder = RunRecorder(self.run_store, run_context)
        await recorder.run_started(
            user_message=request.user_message,
            model=request.model or provider_default_model,
            api_family=getattr(active_provider, "api_family", ""),
        )
        self._log_loop_start(
            request,
            active_provider,
            trace,
            provider_default_model,
        )

        history = list(request.history_messages or [])
        active_turn_messages = [
            ContextMessage(
                role="user",
                source="user_input",
                content=request.user_message,
                parts=list(request.user_parts or []),
            )
        ]
        logger.debug(
            "agent_loop.active_turn_started",
            trace_id=trace.trace_id if trace else "",
            history_count=len(history),
            protected_count=len(active_turn_messages),
            protected_roles=[message.role for message in active_turn_messages],
        )
        return _LoopRuntime(
            request=request,
            provider=active_provider,
            context_builder=active_builder,
            trace=trace,
            recorder=recorder,
            effective_system_prompt=effective_system_prompt,
            history=history,
            active_turn_messages=active_turn_messages,
        )

    def _log_loop_start(
        self,
        request: _LoopRequest,
        provider: ChatProvider,
        trace: Trace | None,
        provider_default_model: str,
    ) -> None:
        logger.debug(
            "agent_loop.run",
            trace_id=trace.trace_id if trace else "",
            origin=request.origin,
            provider_name=getattr(provider, "name", ""),
            provider_api_family=getattr(provider, "api_family", ""),
            provider_default_model=provider_default_model,
            model_override=request.model or "",
            max_steps=self.config.max_steps,
            provider_timeout_seconds=self.config.provider_timeout_seconds,
            tool_timeout_seconds=self.config.tool_timeout_seconds,
            history_count=len(request.history_messages or []),
            history_roles=[m.role for m in (request.history_messages or [])[:6]],
            history_sources=[m.source for m in (request.history_messages or [])[:6]],
            user_message_chars=len(request.user_message),
            system_prompt_chars=len(request.system_prompt),
            tool_count=len(request.tools or []),
            tool_names=[tool.name for tool in (request.tools or [])[:30]],
            user_part_types=[part.type for part in (request.user_parts or [])],
            stop_requested=(
                request.stop_event.is_set() if request.stop_event is not None else False
            ),
        )

    async def _cancel_runtime(
        self,
        runtime: _LoopRuntime,
        *,
        steps: int,
        final_response: str,
    ) -> LoopEvent:
        """Record cancellation and build the canonical cancelled event."""
        self._record_terminal_outcome(
            trace=runtime.trace,
            step=steps,
            terminal_state="cancelled",
            reason="cancelled",
        )
        await runtime.recorder.terminal(
            terminal_state="cancelled",
            reason="cancelled",
        )
        return self._cancelled_done_event(
            trace=runtime.trace,
            steps=steps,
            final_response=final_response,
            assistant_messages=runtime.assistant_messages,
            tool_messages=runtime.tool_messages,
            ordered_transcript=runtime.active_turn_messages,
            total_usage=runtime.total_usage,
        )

    async def _complete_without_tools(
        self,
        runtime: _LoopRuntime,
        *,
        response: ProviderResponse,
        display: str,
    ) -> LoopEvent:
        """Finalize a clean provider response that requested no more tools."""
        self._log_terminal_without_tool_calls(
            response=response,
            tools=runtime.request.tools,
            step=runtime.step,
            trace=runtime.trace,
        )
        protocol_anomaly = response.tool_protocol_anomaly or ""
        completion_reason = (
            "tool_calls_completed" if runtime.tool_messages else "no_tool_calls"
        )
        usage = runtime.total_usage
        logger.info(
            "agent_loop.run_completed",
            trace_id=runtime.trace.trace_id if runtime.trace else "",
            reason=completion_reason,
            final_response_preview=display[:200],
            terminal_state="completed",
            step=runtime.step,
            max_steps=self.config.max_steps,
            finish_reason=response.finish_reason or "",
            tool_call_count=0,
            protocol_anomaly=protocol_anomaly,
            total_input_tokens=usage.input_tokens,
            total_output_tokens=usage.output_tokens,
            total_cached_tokens=usage.cached_tokens,
            total_reasoning_tokens=usage.reasoning_tokens,
        )
        self._record_terminal_outcome(
            trace=runtime.trace,
            step=runtime.step,
            terminal_state="completed",
            reason=completion_reason,
            finish_reason=response.finish_reason or "",
            tool_call_count=0,
            protocol_anomaly=protocol_anomaly,
        )
        await runtime.recorder.terminal(
            terminal_state="completed",
            reason=completion_reason,
            finish_reason=response.finish_reason or "",
        )
        return self._done_event(
            runtime,
            final_response=display,
            terminal_state="completed",
            terminal_reason=completion_reason,
        )

    @staticmethod
    def _done_event(
        runtime: _LoopRuntime,
        *,
        final_response: str,
        terminal_state: str,
        terminal_reason: str,
        error: str | None = None,
    ) -> LoopEvent:
        """Build a terminal event from the canonical mutable runtime state."""
        return LoopEvent(
            type="done",
            final_response=final_response,
            assistant_messages=list(runtime.assistant_messages),
            tool_messages=list(runtime.tool_messages),
            ordered_transcript=list(runtime.active_turn_messages),
            steps=runtime.step,
            trace_id=runtime.trace.trace_id if runtime.trace else None,
            error=error,
            total_usage=runtime.total_usage,
            terminal_state=terminal_state,
            terminal_reason=terminal_reason,
        )

    async def _max_steps_event(self, runtime: _LoopRuntime) -> LoopEvent:
        """Finalize a run that exhausted the configured step budget."""
        final_response = self._latest_assistant_content(runtime)
        usage = runtime.total_usage
        logger.warning(
            "agent_loop.run_completed",
            trace_id=runtime.trace.trace_id if runtime.trace else "",
            reason="max_steps_reached",
            final_response_preview=final_response[:200],
            terminal_state="incomplete",
            step=self.config.max_steps,
            max_steps=self.config.max_steps,
            assistant_message_count=len(runtime.assistant_messages),
            tool_message_count=len(runtime.tool_messages),
            total_input_tokens=usage.input_tokens,
            total_output_tokens=usage.output_tokens,
            total_cached_tokens=usage.cached_tokens,
            total_reasoning_tokens=usage.reasoning_tokens,
        )
        self._record_terminal_outcome(
            trace=runtime.trace,
            step=self.config.max_steps,
            terminal_state="incomplete",
            reason="max_steps_reached",
            tool_call_count=0,
        )
        await runtime.recorder.terminal(
            terminal_state="incomplete",
            reason="max_steps_reached",
        )
        runtime.step = self.config.max_steps
        return self._done_event(
            runtime,
            final_response=final_response,
            terminal_state="incomplete",
            terminal_reason="max_steps_reached",
        )

    async def _stop_during_provider_event(
        self,
        runtime: _LoopRuntime,
    ) -> LoopEvent:
        """Finalize a stop raised while the provider request was in flight."""
        completed_steps = max(runtime.step - 1, 0)
        final_response = self._latest_assistant_content(runtime)
        logger.info(
            "agent_loop.run_completed",
            trace_id=runtime.trace.trace_id if runtime.trace else "",
            reason="cancelled",
            final_response_preview=final_response[:200],
            terminal_state="cancelled",
            step=runtime.step,
            max_steps=self.config.max_steps,
        )
        return await self._cancel_runtime(
            runtime,
            steps=completed_steps,
            final_response=final_response,
        )

    async def _provider_error_event(
        self,
        runtime: _LoopRuntime,
        error: ProviderError,
    ) -> LoopEvent:
        """Finalize a provider failure with any earlier assistant fallback."""
        logger.warning(
            "agent_loop.provider_error_abort",
            error=str(error),
            exc_info=True,
        )
        fallback = self._latest_assistant_content(runtime)
        usage = runtime.total_usage
        logger.warning(
            "agent_loop.run_completed",
            trace_id=runtime.trace.trace_id if runtime.trace else "",
            reason="provider_error",
            final_response_preview=fallback[:200],
            terminal_state="failed",
            step=runtime.step,
            max_steps=self.config.max_steps,
            error_code=error.code,
            total_input_tokens=usage.input_tokens,
            total_output_tokens=usage.output_tokens,
            total_cached_tokens=usage.cached_tokens,
            total_reasoning_tokens=usage.reasoning_tokens,
        )
        self._record_terminal_outcome(
            trace=runtime.trace,
            step=runtime.step,
            terminal_state="failed",
            reason="provider_error",
        )
        await runtime.recorder.terminal(
            terminal_state="failed",
            reason="provider_error",
            failure_code=error.code,
        )
        if not fallback:
            fallback = self.config.provider_error_template.format(code=error.code)
        return self._done_event(
            runtime,
            final_response=fallback,
            terminal_state="failed",
            terminal_reason="provider_error",
            error=error.code,
        )

    @staticmethod
    def _latest_assistant_content(runtime: _LoopRuntime) -> str:
        if not runtime.assistant_messages:
            return ""
        return runtime.assistant_messages[-1].content

    def _record_terminal_outcome(
        self,
        *,
        trace: Trace | None,
        step: int,
        terminal_state: str,
        reason: str,
        finish_reason: str = "",
        tool_call_count: int = 0,
        protocol_anomaly: str = "",
    ) -> None:
        """Record terminal-outcome telemetry (agent-loop repair Phase 0).

        Pure observability: bumps metrics counters only. It never changes the
        emitted ``LoopEvent`` or the returned ``AgentRunResult`` — Phase 0 keeps
        today's behaviour identical and only makes *why* a run ended queryable.
        """
        if trace is None or self.metrics is None:
            return
        self.metrics.record_terminal_outcome(
            trace,
            step=step,
            terminal_state=terminal_state,
            reason=reason,
            finish_reason=finish_reason,
            tool_call_count=tool_call_count,
            protocol_anomaly=protocol_anomaly,
        )

    def _cancelled_done_event(
        self,
        *,
        trace: Trace | None,
        steps: int,
        final_response: str,
        assistant_messages: list[ContextMessage],
        tool_messages: list[ContextMessage],
        ordered_transcript: list[ContextMessage],
        total_usage: TokenUsage,
    ) -> LoopEvent:
        """Build the cancelled ``done`` event shared by all three stop exits.

        Centralized so every cancelled exit — top-of-step, post-response, and
        mid-call (``_StopRequested``) — carries ``trace_id`` (previously
        omitted, which silently skipped transcript persistence for cancelled
        runs) and identical field shape. Also stamps the trusted
        ``terminal_state="cancelled"`` (issue #42) so downstream task ledgers
        can't misclassify a cancelled run as succeeded based on a non-empty
        fallback text.
        """
        return LoopEvent(
            type="done",
            final_response=final_response,
            assistant_messages=list(assistant_messages),
            tool_messages=list(tool_messages),
            ordered_transcript=list(ordered_transcript),
            steps=steps,
            trace_id=trace.trace_id if trace else None,
            error="cancelled",
            total_usage=total_usage,
            terminal_state="cancelled",
            terminal_reason="cancelled",
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
        stop_event: asyncio.Event | None = None,
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
                response = await self._call_chat_interruptible(
                    active_provider,
                    messages=messages,
                    tools=tools,
                    timeout_seconds=self.config.provider_timeout_seconds,
                    model=model,
                    stop_event=stop_event,
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
                    reasoning_chars=len(response.reasoning_content or ""),
                    refusal_chars=len(response.refusal or ""),
                    usage_input_tokens=(
                        response.usage.input_tokens if response.usage else 0
                    ),
                    usage_output_tokens=(
                        response.usage.output_tokens if response.usage else 0
                    ),
                    usage_cached_tokens=(
                        response.usage.cached_tokens if response.usage else 0
                    ),
                    usage_reasoning_tokens=(
                        response.usage.reasoning_tokens if response.usage else 0
                    ),
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
                    provider_api_family=getattr(active_provider, "api_family", ""),
                    requested_model=model or "",
                    effective_model=model or getattr(active_provider, "model", ""),
                    step=step,
                    attempt=attempts,
                    max_attempts=self.config.retry_attempts + 1,
                    latency_seconds=round(time.monotonic() - t0, 3),
                    error_code=exc.code,
                    error=str(exc),
                    retryable=exc.retryable,
                    will_retry=can_retry,
                    exc_info=not can_retry,
                )
                if not can_retry:
                    raise
                if stop_event is not None:
                    # Honor a stop that arrived during the failed call, and make
                    # the backoff itself interruptible so /stop during retry is
                    # prompt (the sleep is otherwise a second uninterruptible
                    # await). wait_for returns early if stop fires first; a
                    # timeout means the backoff elapsed without a stop.
                    if stop_event.is_set():
                        raise _StopRequested
                    try:
                        await asyncio.wait_for(
                            stop_event.wait(),
                            timeout=self.config.retry_backoff_seconds,
                        )
                    except TimeoutError:
                        pass
                    if stop_event.is_set():
                        raise _StopRequested
                else:
                    await asyncio.sleep(self.config.retry_backoff_seconds)

    async def _call_chat_interruptible(
        self,
        provider: ChatProvider,
        *,
        messages: list[ContextMessage],
        tools: list[ToolDefinition] | None,
        timeout_seconds: float,
        model: str | None,
        stop_event: asyncio.Event | None,
    ) -> ProviderResponse:
        """Call ``provider.chat``, aborting mid-call if ``stop_event`` fires.

        Without this the provider HTTP request is a single uninterruptible
        await that dominates run wall-clock, so ``/stop`` only took effect
        after the call returned (production logs showed an 18.5s call where
        stop set 2s in was honored 16.8s late). Here the call is raced against
        ``stop_event.wait()``; if stop wins the in-flight task is cancelled
        (httpx aborts the request; the shared client survives per-request
        cancellation) and ``_StopRequested`` is raised so ``run_stream`` emits
        the cancelled ``done`` event with the partial turn. If the call wins,
        its result — or ``ProviderError`` — is returned/raised for the normal
        retry path.
        """
        chat_coro: Awaitable[ProviderResponse] = provider.chat(
            messages=messages,
            tools=tools,
            timeout_seconds=timeout_seconds,
            model=model,
        )
        if stop_event is None:
            return await chat_coro

        chat_task = asyncio.ensure_future(chat_coro)
        stop_task = asyncio.ensure_future(stop_event.wait())
        try:
            await asyncio.wait(
                {chat_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:
            # The run task itself was cancelled; clean up both children.
            chat_task.cancel()
            stop_task.cancel()
            with suppress(asyncio.CancelledError):
                await chat_task
            with suppress(asyncio.CancelledError):
                await stop_task
            raise

        if stop_event.is_set():
            chat_task.cancel()
            with suppress(asyncio.CancelledError):
                await chat_task
            stop_task.cancel()
            with suppress(asyncio.CancelledError):
                await stop_task
            raise _StopRequested

        # Chat finished first; cancel the stop waiter and surface the result
        # (or ProviderError) for the retry path.
        stop_task.cancel()
        with suppress(asyncio.CancelledError):
            await stop_task
        return chat_task.result()

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
        recorder: RunRecorder | None = None,
        sender_account_key: str = "",
    ) -> list[ContextMessage]:
        messages: list[ContextMessage] = []

        definitions = self._index_tools(tools)
        logger.debug(
            "agent_loop.tool_batch_start",
            trace_id=trace.trace_id if trace else "",
            step=step,
            requested_tool_count=len(response.tool_calls),
            requested_tools=[
                {
                    "name": tool_call.name,
                    "call_id": tool_call.call_id,
                    "argument_keys": sorted(tool_call.arguments.keys()),
                    "argument_count": len(tool_call.arguments),
                }
                for tool_call in response.tool_calls
            ],
            registered_tool_count=len(definitions),
        )
        for tool_call in response.tool_calls:
            validation_error = self._validate_tool_call(
                tool_call=tool_call,
                definitions=definitions,
            )
            if validation_error is not None:
                logger.warning(
                    "agent_loop.tool_call_rejected",
                    trace_id=trace.trace_id if trace else "",
                    step=step,
                    tool_name=tool_call.name,
                    tool_call_id=tool_call.call_id,
                    argument_keys=sorted(tool_call.arguments.keys()),
                    error_code=validation_error.error_code or "",
                    error_message=validation_error.error_message or "",
                )
                messages.append(
                    self._build_tool_message(
                        tool_call=tool_call,
                        phase="prepare_failed",
                        attempt=0,
                        result=validation_error,
                    )
                )
                if recorder is not None:
                    await recorder.end_tool_call(
                        tool_call, validation_error, attempt=0, phase="prepare_failed"
                    )
                continue

            if recorder is not None:
                await recorder.begin_tool_call(tool_call)
            result, attempt, phase = await self._execute_tool_with_lifecycle(
                tool_call,
                step=step,
                trace=trace,
                sender_account_key=sender_account_key,
            )
            messages.append(
                self._build_tool_message(
                    tool_call=tool_call,
                    phase=phase,
                    attempt=attempt,
                    result=result,
                )
            )
            if recorder is not None:
                await recorder.end_tool_call(
                    tool_call, result, attempt=attempt, phase=phase
                )
        return messages

    async def _execute_tool_with_lifecycle(
        self,
        tool_call: ToolCall,
        *,
        step: int = 0,
        trace: Trace | None = None,
        sender_account_key: str = "",
    ) -> tuple[ToolExecutionResult, int, str]:
        if self.tool_executor is None:
            raise RuntimeError("Tool executor is not set")
        # Phase A action-authorization: privileged tools require an admin sender.
        # Runs at the tool-dispatch boundary, before any execution attempt. A
        # denied call short-circuits to a clean tool error the model can see;
        # memory subsystem code is never involved (decoupling, see authz module).
        if self.authorization is not None:
            # Lazy import avoids the loop → identity → plugins → loop cycle.
            from nahida_bot.identity.authorization import NotAuthorized

            try:
                self.authorization.authorize(
                    tool_call.name,
                    sender_account_key,
                    tool_call.arguments,
                    requires_admin=self.tool_executor.tool_requires_admin(
                        tool_call.name
                    ),
                )
            except NotAuthorized:
                logger.warning(
                    "agent_loop.tool_not_authorized",
                    trace_id=trace.trace_id if trace else "",
                    step=step,
                    tool_name=tool_call.name,
                    sender_account_key=sender_account_key,
                )
                return (
                    ToolExecutionResult.error(
                        code="not_authorized",
                        message=(
                            f"Tool '{tool_call.name}' requires admin "
                            "authorization. The action was not executed."
                        ),
                        retryable=False,
                    ),
                    0,
                    "not_authorized",
                )
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
                argument_keys=sorted(tool_call.arguments.keys()),
                argument_count=len(tool_call.arguments),
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
                logger.exception(
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
                result_summary=self._tool_result_summary(result),
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

    def _message_summary(
        self,
        messages: list[ContextMessage],
    ) -> list[dict[str, object]]:
        return [
            {
                "role": message.role,
                "source": message.source,
                "content_chars": len(message.content),
                "part_types": [part.type for part in message.parts],
                "has_reasoning": bool(message.reasoning),
                "metadata_keys": (
                    sorted(message.metadata.keys()) if message.metadata else []
                ),
            }
            for message in messages
        ]

    def _provider_response_summary(
        self,
        response: ProviderResponse,
    ) -> dict[str, object]:
        usage = response.usage
        return {
            "finish_reason": response.finish_reason or "",
            "content_chars": len(response.content or ""),
            "display_chars": len(self._display_content(response)),
            "reasoning_chars": len(response.reasoning_content or ""),
            "refusal_chars": len(response.refusal or ""),
            "tool_call_count": len(response.tool_calls),
            "tool_names": [tool_call.name for tool_call in response.tool_calls],
            "tool_call_ids": [tool_call.call_id for tool_call in response.tool_calls],
            "extra_keys": sorted(response.extra.keys()),
            "usage_input_tokens": usage.input_tokens if usage else 0,
            "usage_output_tokens": usage.output_tokens if usage else 0,
            "usage_cached_tokens": usage.cached_tokens if usage else 0,
            "usage_reasoning_tokens": usage.reasoning_tokens if usage else 0,
            "raw_response_summary": self._raw_response_summary(response.raw_response),
        }

    def _tool_result_summary(
        self,
        result: ToolExecutionResult,
    ) -> dict[str, object]:
        output = result.output
        if isinstance(output, str):
            output_summary: dict[str, object] = {
                "type": "str",
                "chars": len(output),
            }
        elif isinstance(output, dict):
            output_summary = {
                "type": "dict",
                "keys": sorted(str(key) for key in output.keys())[:30],
            }
        elif isinstance(output, list):
            output_summary = {
                "type": "list",
                "items": len(output),
            }
        elif output is None:
            output_summary = {"type": "none"}
        else:
            output_summary = {"type": type(output).__name__}

        return {
            "is_error": result.is_error,
            "error_code": result.error_code or "",
            "retryable": result.retryable,
            "log_count": len(result.logs),
            "log_chars": sum(len(log) for log in result.logs),
            "output": output_summary,
        }

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
