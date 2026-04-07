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
class ProviderResponse:
    """Normalized provider response used by agent loop."""

    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    raw_response: dict[str, object] | None = None


class ChatProvider(ABC):
    """Common provider interface consumed by agent loop."""

    name: str

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
