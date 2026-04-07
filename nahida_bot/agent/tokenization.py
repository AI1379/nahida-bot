"""Tokenization and provider abstraction for context budgeting."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class Tokenizer(Protocol):
    """Tokenizer protocol used by context budgeting."""

    def count_tokens(self, text: str) -> int:
        """Return estimated token count for input text."""
        ...


class HeuristicTokenizer:
    """General-purpose tokenizer approximation using lexical chunks."""

    _pattern = re.compile(r"[A-Za-z0-9_]+|[^\w\s]", re.UNICODE)

    def count_tokens(self, text: str) -> int:
        """Estimate token count by lexical chunking.

        This keeps behavior deterministic and works without third-party model tokenizers.
        """
        if not text:
            return 0

        tokens = self._pattern.findall(text)
        if tokens:
            return len(tokens)

        # Fallback for scripts where the simple regex may miss chunk boundaries.
        return max(1, math.ceil(len(text) / 4))


@dataclass(slots=True, frozen=True)
class CharacterEstimateTokenizer:
    """Character-based estimator used as final fallback path."""

    chars_per_token: int = 4

    def count_tokens(self, text: str) -> int:
        """Estimate token count via a fixed char-to-token ratio."""
        if not text:
            return 0
        ratio = self.chars_per_token if self.chars_per_token > 0 else 4
        return max(1, math.ceil(len(text) / ratio))


class CompositeTokenizer:
    """Tokenizer that tries one tokenizer and falls back on another."""

    def __init__(self, primary: Tokenizer, fallback: Tokenizer) -> None:
        self.primary = primary
        self.fallback = fallback

    def count_tokens(self, text: str) -> int:
        """Count tokens with primary tokenizer, fallback on runtime failure."""
        try:
            return self.primary.count_tokens(text)
        except Exception:
            return self.fallback.count_tokens(text)


def resolve_tokenizer(
    *,
    provider_tokenizer: Tokenizer | None,
    tokenizer: Tokenizer | None,
    fallback_tokenizer: Tokenizer | None,
) -> Tokenizer:
    """Resolve tokenizer priority: explicit > provider > fallback > char estimate."""
    if tokenizer is not None:
        if fallback_tokenizer is not None:
            return CompositeTokenizer(tokenizer, fallback_tokenizer)
        return tokenizer

    if provider_tokenizer is not None:
        fallback = fallback_tokenizer or HeuristicTokenizer()
        return CompositeTokenizer(provider_tokenizer, fallback)

    if fallback_tokenizer is not None:
        return fallback_tokenizer

    return CompositeTokenizer(HeuristicTokenizer(), CharacterEstimateTokenizer())
