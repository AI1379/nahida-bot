"""GLM/ZhiPu provider — fully OpenAI-compatible, no special handling needed."""

from __future__ import annotations

from dataclasses import dataclass

from nahida_bot.agent.providers.openai_compatible import OpenAICompatibleProvider
from nahida_bot.agent.providers.registry import register_provider


@register_provider("glm", "GLM/ZhiPu Provider")
@dataclass(slots=True)
class GLMProvider(OpenAICompatibleProvider):
    """GLM Provider.

    Fully OpenAI-compatible (``/api/paas/v4/chat/completions``).
    No reasoning chain or special fields to handle.
    """

    name: str = "glm"
