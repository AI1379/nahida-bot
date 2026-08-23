"""Domain lexicon, digit-merge, and entity-alias query expansion tests."""

from __future__ import annotations

import pytest

from nahida_bot.agent.storage.sqlite_document_store import SQLiteDocumentStore
from nahida_bot.agent.storage.tokenization import (
    alias_terms,
    build_fts_and_query,
    build_fts_query,
    extract_keywords,
)
from nahida_bot.db.engine import DatabaseEngine


# ---------------------------------------------------------------------------
# Domain lexicon (Tier 1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("term", "expected_fragment"),
    [
        ("纳西妲被囚禁在净善宫五百年", "纳西妲"),
        ("大慈树王创造了世界树", "世界树"),
        ("教令院的学者", "教令院"),
        ("愚人众执行官", "愚人众"),
    ],
)
def test_domain_terms_survive_segmentation(term: str, expected_fragment: str) -> None:
    """Domain lexicon terms must be emitted as whole tokens (issue #49:
    纳西妲/世界树 had zero FTS hits because jieba fragmented them)."""
    keywords = extract_keywords(term)
    assert expected_fragment in keywords


def test_domain_terms_keep_fragment_granularity_for_compat() -> None:
    """Search mode emits both granularities, so pre-retokenization indexes
    still match fragment queries (纳西) and post-retokenization indexes
    match whole-term queries (纳西妲)."""
    keywords = extract_keywords("纳西妲")
    assert "纳西妲" in keywords
    assert "纳西" in keywords


def test_digit_tokens_merge_into_preceding_cjk_token() -> None:
    keywords = extract_keywords("角色故事3")
    assert "故事3" in keywords
    assert "3" not in keywords  # never a standalone single-char index term


# ---------------------------------------------------------------------------
# Entity alias expansion (Tier 2)
# ---------------------------------------------------------------------------


def test_alias_terms_expand_detected_entities() -> None:
    extras = alias_terms("草神对旅行者的态度")
    assert '"纳西妲"' in extras
    assert '"小吉祥草王"' in extras


def test_alias_terms_expand_in_reverse_direction() -> None:
    extras = alias_terms("纳西妲的世界树")
    assert '"草神"' in extras


def test_alias_terms_absent_for_undetected_entities() -> None:
    assert alias_terms("今天天气怎么样") == []


def test_and_form_never_contains_aliases() -> None:
    query = "草神对旅行者的态度"
    and_form = build_fts_and_query(query)
    assert "纳西妲" not in and_form
    # OR form carries them.
    or_form = build_fts_query(query)
    assert '"纳西妲"' in alias_terms(query)
    assert " OR " in or_form


@pytest.mark.asyncio
async def test_store_search_matches_doc_via_alias() -> None:
    """A query using an alias must surface a document that only uses the
    canonical name (via the OR fallback tier)."""
    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    try:
        store = SQLiteDocumentStore(engine, collection="kblex")
        await store.setup()
        await store.put(
            "doc_nahida",
            "纳西妲安静地待在净善宫里，翻阅着世界的记忆",
            title="净善宫日常",
            node_type="passage",
        )
        results = await store.search("草神 净善宫", limit=5)
        assert [r.doc_id for r in results] == ["doc_nahida"]
    finally:
        await engine.close()


# ---------------------------------------------------------------------------
# Retokenize script (Tier 1 deployment)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retokenize_rebuilds_stale_fts_rows(tmp_path):
    from scripts.retokenize_kb_fts import retokenize_collection

    engine = DatabaseEngine(str(tmp_path / "retok.sqlite3"))
    await engine.initialize()
    try:
        store = SQLiteDocumentStore(engine, collection="kbretok")
        await store.setup()
        await store.put(
            "doc_1",
            "纳西妲被囚禁在净善宫五百年，角色故事3记载了这段往事",
            title="故事",
            node_type="passage",
        )
        # Corrupt the FTS row to simulate the pre-lexicon tokenizer: no whole
        # domain terms, no merged digit tokens.
        async with engine.write_lock:
            await engine.execute(
                "UPDATE kbretok_doc_fts SET content_index = '纳西 囚禁 在 净善宫 五百 年'"
                " WHERE doc_id = 'doc_1'"
            )
            await engine.db.commit()

        changed, total = await retokenize_collection(engine, "kbretok", dry_run=False)
        assert (changed, total) == (1, 1)

        results = await store.search("纳西妲 净善宫", limit=5)
        assert [r.doc_id for r in results] == ["doc_1"]
        # Merged digit token is searchable after the rebuild.
        results = await store.search("角色故事3", limit=5)
        assert results, "digit-merged token must match after re-tokenization"
    finally:
        await engine.close()
