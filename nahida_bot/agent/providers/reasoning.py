"""Shared reasoning extraction logic for OpenAI-compatible providers."""

from __future__ import annotations

from typing import Mapping
import re

# Re-export ReasoningPolicy from context (canonical location) so that
# reasoning.py consumers don't need to know the internal layout.
from nahida_bot.agent.context import ReasoningPolicy

# <think/> / <thinking/> tag pattern — covers DeepSeek, Groq, and other
# providers that embed reasoning inside content using XML-like tags.
_THINK_TAG_PATTERN = re.compile(
    r"<think(?:ing)?\s*>(.*?)</think(?:ing)?\s*>", re.DOTALL
)


def extract_think_tags(content: str) -> tuple[str, str | None]:
    """Extract and remove ``<think/>`` / ``<thinking/>`` tags from content.

    Args:
        content: Raw content string that may contain think tags.

    Returns:
        A tuple of *(cleaned_content, extracted_reasoning)*.
        ``extracted_reasoning`` is ``None`` when no tags are found.
    """
    if not content:
        return content, None

    matches = _THINK_TAG_PATTERN.findall(content)
    if not matches:
        return content, None

    reasoning = "\n".join(match.strip() for match in matches if match.strip())
    cleaned = _THINK_TAG_PATTERN.sub("", content).strip()
    return cleaned, reasoning or None


class ReasoningMixin:
    """Shared reasoning extraction for the OpenAI-compatible provider family.

    Subclasses can override ``reasoning_key`` to match their specific
    response field name (e.g. ``"reasoning"`` for Groq).
    """

    reasoning_key: str = "reasoning_content"

    def _extract_reasoning_from_message(
        self, message: Mapping[str, object]
    ) -> tuple[str | None, str | None]:
        """Extract reasoning content from a response message dict.

        Priority:
            1. Structured field (``self.reasoning_key``)
            2. ``<think/>`` tag extraction from ``content``

        Returns:
            A tuple of *(reasoning_content, cleaned_content_or_none)*.
            ``cleaned_content_or_none`` is only set when think tags were
            found inside ``content`` and have been stripped — the caller
            **must** use this cleaned value instead of the original content.
        """
        # Priority 1: native structured field
        raw = message.get(self.reasoning_key)
        if isinstance(raw, str) and raw.strip():
            return raw, None

        # Priority 2: tag-based extraction from content
        content = message.get("content")
        if isinstance(content, str):
            cleaned, reasoning = extract_think_tags(content)
            if reasoning:
                return reasoning, cleaned

        return None, None


__all__ = [
    "ReasoningPolicy",
    "ReasoningMixin",
    "extract_think_tags",
]
