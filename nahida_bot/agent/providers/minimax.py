"""Minimax provider — uses Anthropic-compatible Messages API.

Minimax provides an Anthropic-compatible endpoint at
``https://api.minimaxi.com/anthropic`` that supports ``thinking``,
``tool_use``, and ``tool_result`` content blocks natively.
"""

from __future__ import annotations

from dataclasses import dataclass

from nahida_bot.agent.providers.anthropic import AnthropicProvider
from nahida_bot.agent.providers.registry import register_provider


@register_provider("minimax", "Minimax Provider")
@dataclass(slots=True)
class MinimaxProvider(AnthropicProvider):
    """Minimax Provider using Anthropic-compatible Messages API.

    Inherits full Anthropic protocol support including ``thinking`` blocks,
    ``tool_use``/``tool_result`` serialization, and structured content blocks.

    Set ``base_url`` to ``https://api.minimaxi.com/anthropic`` in config.
    """

    name: str = "minimax"
