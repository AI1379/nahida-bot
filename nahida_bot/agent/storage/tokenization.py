"""Text tokenization utilities for FTS indexing and query building.

Provides CJK-aware keyword extraction (via jieba) and FTS5 query construction.
Shared by the memory system and the generic document store.
"""

from __future__ import annotations

import re
import warnings

# FIXME: jieba 0.42.1 emits SyntaxWarning on Python 3.12+ due to invalid escapes.
# Keep this suppression until we upgrade/patch jieba in a dedicated follow-up.
# TODO: jieba's dictionary loading costs ~0.5-1s at import time. Even if the
# memory subsystem is never used, this module-level import pays that cost.
# Consider lazy-loading: defer ``import jieba`` into ``extract_keywords()``
# on first call, or use ``functools.lru_cache`` on a wrapper.
with warnings.catch_warnings():
    warnings.simplefilter("ignore", SyntaxWarning)
    import jieba

_MIN_KEYWORD_LENGTH = 2
_CJK_RANGE = re.compile(r"[一-鿿㐀-䶿가-힯]")
_KEYWORD_SPLIT = re.compile(r"[^\w]+", re.UNICODE)
_FTS_SPECIAL = re.compile(r'["\s]+')


def extract_keywords(text: str, *, min_length: int = _MIN_KEYWORD_LENGTH) -> list[str]:
    """Extract normalized keywords from text for indexing.

    Uses jieba for CJK segmentation and whitespace splitting for Latin text.
    Preserves first-occurrence order with stable deduplication.
    """
    if not text:
        return []

    has_cjk = bool(_CJK_RANGE.search(text))

    if has_cjk:
        # jieba cut_for_search produces fine-grained tokens suitable for indexing.
        raw_tokens = jieba.lcut_for_search(text)
    else:
        raw_tokens = _KEYWORD_SPLIT.split(text.lower())

    seen: set[str] = set()
    result: list[str] = []
    for token in raw_tokens:
        token = token.strip().lower()
        if len(token) >= min_length and token not in seen:
            seen.add(token)
            result.append(token)
    return result


def tokenize_for_fts(text: str) -> str:
    """Tokenize text into a space-separated FTS index string.

    SQLite FTS5's BM25 ranking is useful, but its default tokenizer is not a
    Chinese segmenter. We pre-tokenize CJK text with jieba search mode and store
    the resulting tokens as an ASCII-space-separated index field.
    """
    return " ".join(extract_keywords(text))


def build_fts_query(query: str) -> str:
    """Build a safe OR query for pre-tokenized FTS fields."""
    tokens = extract_keywords(query)
    quoted: list[str] = []
    for token in tokens:
        cleaned = _FTS_SPECIAL.sub(" ", token).strip()
        if cleaned:
            quoted.append(f'"{cleaned}"')
    return " OR ".join(quoted)
