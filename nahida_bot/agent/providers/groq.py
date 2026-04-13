"""Groq provider — uses ``reasoning`` key and strips reasoning from history."""

from __future__ import annotations

from dataclasses import dataclass

from nahida_bot.agent.context import ContextMessage
from nahida_bot.agent.providers.openai_compatible import OpenAICompatibleProvider
from nahida_bot.agent.providers.registry import register_provider


@register_provider("groq", "Groq Provider")
@dataclass(slots=True)
class GroqProvider(OpenAICompatibleProvider):
    """Groq Provider.

    Uses ``"reasoning"`` as the structured reasoning field name
    (unlike the default ``"reasoning_content"``).

    Groq also requires stripping reasoning fields from serialized
    assistant messages in history, as the API rejects them.
    """

    name: str = "groq"
    reasoning_key: str = "reasoning"

    def serialize_messages(
        self, messages: list[ContextMessage]
    ) -> list[dict[str, object]]:
        """Serialize messages, stripping reasoning from assistant history."""
        serialized = OpenAICompatibleProvider.serialize_messages(self, messages)
        for msg in serialized:
            if msg.get("role") == "assistant":
                msg.pop("reasoning_content", None)
                msg.pop("reasoning", None)
        return serialized
