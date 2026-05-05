"""Provider contracts for agent loop."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

from nahida_bot.agent.context import ContextMessage, ContextPart
from nahida_bot.agent.tokenization import Tokenizer

ToolType = Literal["function"]


@dataclass(slots=True, frozen=True)
class ModelCapabilities:
    """Declares what a specific provider/model combination can do.

    Resolution priority: explicit config > provider defaults > unknown defaults to off.
    """

    text_input: bool = True
    image_input: bool = False
    tool_calling: bool = True
    reasoning: bool = False
    prompt_cache: bool = False
    prompt_cache_images: bool = False
    explicit_context_cache: bool = False
    prompt_cache_min_tokens: int = 0
    max_image_count: int = 0
    max_image_bytes: int = 0
    supported_image_mime_types: tuple[str, ...] = (
        "image/jpeg",
        "image/png",
        "image/webp",
    )


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
    cache_creation_tokens: int = 0

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
        model: str | None = None,
    ) -> ProviderResponse:
        """Run a single chat completion round.

        Args:
            model: Override the default model for this request only.
        """
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
            "content": self._serialize_openai_content(message),
        }
        return payload

    def _serialize_openai_content(self, message: ContextMessage) -> object:
        """Serialize text/image parts using OpenAI-compatible content blocks."""
        if not message.parts:
            return message.content

        blocks: list[dict[str, object]] = []
        for part in message.parts:
            block = self._serialize_openai_part(part)
            if block is not None:
                blocks.append(block)

        if blocks and all(block.get("type") == "text" for block in blocks):
            return "\n".join(
                str(block.get("text", "")) for block in blocks if block.get("text")
            )
        return blocks or message.content

    def _serialize_openai_part(self, part: ContextPart) -> dict[str, object] | None:
        if part.type in {"text", "image_description"}:
            if not part.text:
                return None
            return {"type": "text", "text": part.text}

        if part.type == "image_url":
            if not part.url:
                return None
            return {"type": "image_url", "image_url": {"url": part.url}}

        if part.type == "image_base64":
            if not part.data or not part.mime_type:
                return None
            return {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{part.mime_type};base64,{part.data}",
                },
            }

        return None
