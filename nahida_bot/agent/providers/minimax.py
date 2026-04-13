"""Minimax provider — OpenAI-compatible, no special handling needed."""

from __future__ import annotations

from dataclasses import dataclass

from nahida_bot.agent.providers.openai_compatible import OpenAICompatibleProvider
from nahida_bot.agent.providers.registry import register_provider


@register_provider("minimax", "Minimax Provider")
@dataclass(slots=True)
class MinimaxProvider(OpenAICompatibleProvider):
    """Minimax Provider.

    Uses ``/v1/text/chatcompletion_v2`` path (non-standard but
    OpenAI-compatible format). The ``base_url`` should point to the
    appropriate endpoint.
    """

    name: str = "minimax"
