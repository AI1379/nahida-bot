"""DeepSeek provider — OpenAI-compatible with ``reasoning_content`` field."""

from __future__ import annotations

from dataclasses import dataclass

from nahida_bot.agent.providers.openai_compatible import OpenAICompatibleProvider
from nahida_bot.agent.providers.registry import register_provider


@register_provider("deepseek", "DeepSeek Provider")
@dataclass(slots=True)
class DeepSeekProvider(OpenAICompatibleProvider):
    """DeepSeek Provider.

    DeepSeek-R1 and DeepSeek-Chat (V3.2 with ``thinking`` enabled) both use
    ``reasoning_content`` as the structured reasoning field — identical to
    the ``OpenAICompatibleProvider`` default, so no override is needed.
    """

    name: str = "deepseek"
