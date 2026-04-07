"""Agent loop orchestration for provider and tool execution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Protocol

from nahida_bot.agent.context import ContextBuilder, ContextMessage
from nahida_bot.agent.providers import (
    ChatProvider,
    ProviderError,
    ProviderResponse,
    ToolCall,
    ToolDefinition,
)


class ToolExecutor(Protocol):
    """Executor contract for tool calls emitted by providers."""

    async def execute(self, tool_call: ToolCall) -> str:
        """Execute a tool call and return textual result."""
        ...


@dataclass(slots=True, frozen=True)
class AgentLoopConfig:
    """Config for loop retries and termination conditions."""

    max_steps: int = 8
    provider_timeout_seconds: float = 30.0
    retry_attempts: int = 2
    retry_backoff_seconds: float = 0.2


@dataclass(slots=True, frozen=True)
class AgentRunResult:
    """Result from an agent loop execution."""

    final_response: str
    assistant_messages: list[ContextMessage] = field(default_factory=list)
    tool_messages: list[ContextMessage] = field(default_factory=list)
    steps: int = 0


class AgentLoop:
    """Minimal agent loop with provider calls, tools, and stop conditions."""

    def __init__(
        self,
        *,
        provider: ChatProvider,
        context_builder: ContextBuilder,
        config: AgentLoopConfig | None = None,
        tool_executor: ToolExecutor | None = None,
    ) -> None:
        self.provider = provider
        self.context_builder = context_builder
        self.config = config or AgentLoopConfig()
        self.tool_executor = tool_executor

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
        conversation = list(history_messages or [])
        conversation.append(
            ContextMessage(role="user", source="user_input", content=user_message)
        )
        tool_messages: list[ContextMessage] = []
        assistant_messages: list[ContextMessage] = []

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
            )

            if response.content:
                assistant_message = ContextMessage(
                    role="assistant",
                    source="provider_response",
                    content=response.content,
                )
                assistant_messages.append(assistant_message)
                conversation.append(assistant_message)

            if not response.tool_calls:
                return AgentRunResult(
                    final_response=response.content or "",
                    assistant_messages=assistant_messages,
                    tool_messages=tool_messages,
                    steps=step,
                )

            if self.tool_executor is None:
                raise RuntimeError(
                    "Provider requested tools but no tool executor is set"
                )

            executed_messages = await self._execute_tools(response)
            tool_messages.extend(executed_messages)
            conversation.extend(executed_messages)

        final_fallback = assistant_messages[-1].content if assistant_messages else ""
        return AgentRunResult(
            final_response=final_fallback,
            assistant_messages=assistant_messages,
            tool_messages=tool_messages,
            steps=self.config.max_steps,
        )

    async def _call_provider_with_retry(
        self,
        *,
        messages: list[ContextMessage],
        tools: list[ToolDefinition] | None,
    ) -> ProviderResponse:
        attempts = 0
        while True:
            attempts += 1
            try:
                return await self.provider.chat(
                    messages=messages,
                    tools=tools,
                    timeout_seconds=self.config.provider_timeout_seconds,
                )
            except ProviderError as exc:
                can_retry = exc.retryable and attempts <= self.config.retry_attempts
                if not can_retry:
                    raise
                await asyncio.sleep(self.config.retry_backoff_seconds)

    async def _execute_tools(self, response: ProviderResponse) -> list[ContextMessage]:
        messages: list[ContextMessage] = []
        assert self.tool_executor is not None

        for tool_call in response.tool_calls:
            result = await self.tool_executor.execute(tool_call)
            messages.append(
                ContextMessage(
                    role="tool",
                    source=f"tool_result:{tool_call.name}",
                    content=result,
                )
            )
        return messages
