"""Text tokenization utilities for FTS indexing and query building.

Provides CJK-aware keyword extraction (via jieba) and FTS5 query construction.
Shared by the memory system and the generic document store.
"""

from __future__ import annotations

import json
import re
import warnings
from functools import lru_cache
from pathlib import Path

# FIXME: jieba 0.42.1 emits SyntaxWarning on Python 3.12+ due to invalid escapes.
# Keep this suppression until we upgrade/patch jieba in a dedicated follow-up.
# TODO: jieba's dictionary loading costs ~0.5-1s at import time. Even if the
# memory subsystem is never used, this module-level import pays that cost.
# Consider lazy-loading: defer ``import jieba`` into ``extract_keywords()``
# on first call, or use ``functools.lru_cache`` on a wrapper.
with warnings.catch_warnings():
    warnings.simplefilter("ignore", SyntaxWarning)
    import jieba

_STORAGE_DIR = Path(__file__).resolve().parent
_DOMAIN_LEXICON_PATH = _STORAGE_DIR / "domain_lexicon.txt"
_ENTITY_ALIASES_PATH = _STORAGE_DIR / "entity_aliases.json"

_MIN_KEYWORD_LENGTH = 2
_CJK_RANGE = re.compile(r"[一-鿿㐀-䶿가-힯]")
_KEYWORD_SPLIT = re.compile(r"[^\w]+", re.UNICODE)
_FTS_SPECIAL = re.compile(r'["\s]+')
_DIGITS_ONLY = re.compile(r"^\d+$")
_CJK_TAIL = re.compile(r"[一-鿿㐀-䶿가-힯]$")


def _load_domain_lexicon() -> int:
    """Register domain terms with jieba so they survive segmentation.

    Terms like 纳西妲/世界树 are fragmented by the stock dictionary
    (纳西+妲, 世界+树), which made them unmatchable as whole FTS tokens.
    ``add_word`` with a high frequency makes ``lcut_for_search`` emit BOTH
    granularities, keeping index/query compatible across a re-tokenization.
    A missing or malformed file must never break the bot — it just means no
    domain boost.
    """
    try:
        lines = _DOMAIN_LEXICON_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    added = 0
    for line in lines:
        term = line.split("#", 1)[0].strip()
        if term:
            jieba.add_word(term, 10000)
            added += 1
    return added


_load_domain_lexicon()


@lru_cache(maxsize=1)
def _entity_alias_groups() -> tuple[tuple[str, ...], ...]:
    """Load entity alias groups for query expansion (see entity_aliases.json)."""
    try:
        raw = json.loads(_ENTITY_ALIASES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    groups = raw.get("groups") if isinstance(raw, dict) else None
    if not isinstance(groups, list):
        return ()
    return tuple(
        tuple(str(m).strip() for m in group if str(m).strip())
        for group in groups
        if isinstance(group, list)
    )


def alias_terms(query: str) -> list[str]:
    """Return quoted FTS terms from alias groups detected in the query.

    When any member of a group appears in the query, the group's other
    members are returned as extra OR-side terms (查询用「草神」时补上
    「纳西妲」等). Callers must only fold these into the OR form — adding
    them to the AND side would require documents to contain every alias.
    """
    extras: list[str] = []
    seen: set[str] = set()
    for group in _entity_alias_groups():
        if not any(member in query for member in group):
            continue
        for member in group:
            if member in query or member in seen:
                continue
            seen.add(member)
            extras.append(f'"{member}"')
    return extras


def _merge_digit_tokens(raw_tokens: list[str]) -> list[str]:
    """Fold digit-only tokens into the preceding CJK token.

    jieba splits 角色故事3 into 角色/故事/3, and single characters are
    filtered by the minimum keyword length — so section numbers never
    reached the index and 角色故事3/4/5 were indistinguishable. Merging
    gives discriminative tokens (故事3, 第3) on both index and query side.
    """
    merged: list[str] = []
    for token in raw_tokens:
        if merged and _DIGITS_ONLY.match(token) and _CJK_TAIL.search(merged[-1]):
            merged[-1] += token
        else:
            merged.append(token)
    return merged


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
        raw_tokens = _merge_digit_tokens(jieba.lcut_for_search(text))
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


def fts_terms(query: str) -> list[str]:
    """Return the cleaned, quoted FTS terms for a query string.

    Shared by the OR and AND query builders so both forms see exactly the
    same term set (index side and query side tokenize identically).
    """
    quoted: list[str] = []
    for token in extract_keywords(query):
        cleaned = _FTS_SPECIAL.sub(" ", token).strip()
        if cleaned:
            quoted.append(f'"{cleaned}"')
    return quoted


def build_fts_query(query: str) -> str:
    """Build a safe OR query for pre-tokenized FTS fields."""
    return " OR ".join(fts_terms(query))


def build_fts_and_query(query: str) -> str:
    """Build a conjunction query requiring every query term.

    Used as a precision-first tier before the OR fallback: keyword-exact
    queries ("七七 角色故事3") rank their true target without keyword
    collisions, while broad semantic queries that match nothing under AND
    fall through to OR (issue #49 root cause 5).
    """
    return " AND ".join(fts_terms(query))
