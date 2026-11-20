"""Tokenization and provider abstraction for context budgeting."""

from __future__ import annotations

import math
import re
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache
from typing import Protocol, runtime_checkable

import structlog
import tiktoken

logger = structlog.get_logger(__name__)

_MAX_UNBROKEN_CHARS = 256
_LONG_TEXT_RUN = re.compile(
    rf"(?<!\S)\S{{{_MAX_UNBROKEN_CHARS + 1},}}|(?<!\s)\s{{{_MAX_UNBROKEN_CHARS + 1},}}"
)


def _bpe_chunks(text: str) -> Iterator[str]:
    """Bound the merge work for long runs of text or whitespace."""
    if len(text) <= _MAX_UNBROKEN_CHARS:
        yield text
        return
    start = 0
    for span in _LONG_TEXT_RUN.finditer(text):
        left, right = span.span()
        if start < left:
            yield text[start:left]
        for offset in range(left, right, _MAX_UNBROKEN_CHARS):
            yield text[offset : min(offset + _MAX_UNBROKEN_CHARS, right)]
        start = right
    if start < len(text):
        yield text[start:]


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


@cache
def _o200k_encoding() -> tiktoken.Encoding | None:
    try:
        return tiktoken.get_encoding("o200k_base")
    except (OSError, ValueError) as exc:
        logger.warning(
            "tokenizer.vocabulary_unavailable", error_type=type(exc).__name__
        )
        return None


class O200kTokenizer:
    """Text token estimate shared by model routes."""

    def count_tokens(self, text: str) -> int:
        if not text:
            return 0
        encoding = _o200k_encoding()
        if encoding is None:
            # Reserve one token per UTF-8 byte if the BPE vocabulary is unavailable.
            return len(text.encode("utf-8", errors="replace"))
        return sum(len(encoding.encode_ordinary(chunk)) for chunk in _bpe_chunks(text))

    def prefix_within_tokens(self, text: str, limit: int) -> str:
        """Take a token prefix, discarding a partial UTF-8 character at the cut."""
        if limit <= 0:
            return ""
        encoding = _o200k_encoding()
        if encoding is None:
            return text.encode("utf-8", errors="replace")[:limit].decode(
                "utf-8", errors="ignore"
            )
        retained_chars = 0
        for chunk in _bpe_chunks(text):
            tokens = encoding.encode_ordinary(chunk)
            if len(tokens) > limit:
                tail = encoding.decode(tokens[:limit], errors="ignore")
                return text[:retained_chars] + tail
            limit -= len(tokens)
            retained_chars += len(chunk)
            if limit == 0:
                return text[:retained_chars]
        return text


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
        fallback = fallback_tokenizer or O200kTokenizer()
        return CompositeTokenizer(provider_tokenizer, fallback)

    if fallback_tokenizer is not None:
        return fallback_tokenizer

    return O200kTokenizer()
