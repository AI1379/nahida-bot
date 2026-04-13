"""Provider contracts for agent loop."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

from nahida_bot.agent.context import ContextMessage
from nahida_bot.agent.tokenization import Tokenizer

ToolType = Literal["function"]


@dataclass(slots=True, frozen=True)
class ToolDefinition:
    """Tool metadata exposed to model providers."""

    name: str
    description: str
    parameters: dict[str, object]
    type: ToolType = "function"


@dataclass(slots=True, frozen=True)
class ToolCall:
    """Tool call emitted by provider response."""

    call_id: str
    name: str
    arguments: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class TokenUsage:
    """Token usage statistics from a provider response."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(slots=True, frozen=True)
class ProviderResponse:
    """Normalized provider response used by agent loop."""

    # Standard fields
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    raw_response: dict[str, object] | None = None

    # Reasoning chain (Phase 2.8)
    reasoning_content: str | None = None
    reasoning_signature: str | None = None
    has_redacted_thinking: bool = False

    # Refusal / safety
    refusal: str | None = None

    # Usage statistics
    usage: TokenUsage | None = None

    # Provider-specific extension bag
    extra: dict[str, object] = field(default_factory=dict)


class ChatProvider(ABC):
    """Common provider interface consumed by agent loop."""

    name: str
    api_family: str = "openai-completions"

    @property
    @abstractmethod
    def tokenizer(self) -> Tokenizer | None:
        """Provider-specific tokenizer for context budgeting."""
        raise NotImplementedError

    @abstractmethod
    async def chat(
        self,
        *,
        messages: list[ContextMessage],
        tools: list[ToolDefinition] | None = None,
        timeout_seconds: float | None = None,
    ) -> ProviderResponse:
        """Run a single chat completion round."""
        raise NotImplementedError

    def format_tools(self, tools: list[ToolDefinition]) -> list[object]:
        """Convert ToolDefinition list to provider-native tool format.

        Default implementation produces the OpenAI format.
        Override in Anthropic/Gemini providers.
        """
        return [
            {
                "type": tool.type,
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tools
        ]

    def serialize_messages(
        self, messages: list[ContextMessage]
    ) -> list[dict[str, object]]:
        """Convert ContextMessage list to provider-native request format.

        Default implementation produces the OpenAI format.
        Override in Anthropic/Gemini providers.
        """
        return [self._serialize_one_message(msg) for msg in messages]

    def _serialize_one_message(self, message: ContextMessage) -> dict[str, object]:
        """Default OpenAI-format serialization for a single message.

        Subclasses may override for provider-specific formats.
        """
        payload: dict[str, object] = {
            "role": message.role,
            "content": message.content,
        }
        return payload
