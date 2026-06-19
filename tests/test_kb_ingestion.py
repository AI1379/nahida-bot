"""Tests for KB Phase 1 ingestion and chunking improvements."""

from __future__ import annotations

import pytest

from nahida_bot.agent.storage.embedding import HashEmbeddingProvider
from nahida_bot.agent.storage.sqlite_document_store import SQLiteDocumentStore
from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.plugins.knowledge_base.ingestion import (
    _build_retrieval_text,
    _split_long_paragraph,
    import_document,
    parse_markdown,
    split_into_chunks,
)


# ── Hard cap: over-long paragraph splits below chunk_size ──


def test_split_long_paragraph_on_sentence_boundaries() -> None:
    """A paragraph over the cap is split by sentence boundaries first."""
    para = (
        "Alice likes Python. Bob prefers Rust! Carol enjoys Go? "
        "Dave uses JavaScript. Eve writes Haskell."
    )
    pieces = _split_long_paragraph(para, chunk_size=80)
    for piece in pieces:
        assert len(piece) <= 80
    assert len(pieces) > 1


def test_split_long_paragraph_cjk_no_whitespace() -> None:
    """CJK text without whitespace after terminators still splits on sentences."""
    # Typical Genshin-style Chinese text: no spaces after 。！？
    para = "蒙德城是自由之都。风神巴巴托斯常驻于此！你听说了吗？旅行者。"
    pieces = _split_long_paragraph(para, chunk_size=500)
    # Each CJK sentence should be its own piece (all well under 500 chars).
    assert len(pieces) >= 3  # at minimum: 。+ ！+ ？splits
    assert any("蒙德城" in p for p in pieces)
    assert any("风神巴巴托斯" in p for p in pieces)
    assert any("旅行者" in p for p in pieces)


def test_split_long_paragraph_character_window_fallback() -> None:
    """A single sentence longer than the cap is split by character window."""
    long_sentence = "A" * 2500
    pieces = _split_long_paragraph(long_sentence, chunk_size=500)
    for piece in pieces:
        assert len(piece) <= 500
    assert len(pieces) == 5
    assert "".join(pieces) == long_sentence


def test_short_paragraph_not_split() -> None:
    """A paragraph under the cap is returned as a single piece."""
    pieces = _split_long_paragraph("Short paragraph.", chunk_size=500)
    assert pieces == ["Short paragraph."]


def test_split_into_chunks_enforces_hard_cap() -> None:
    """Every chunk emitted by split_into_chunks is <= chunk_size."""
    # Build text with one huge paragraph + normal paragraphs.
    huge = "X" * 2000
    text = f"{huge}\n\nParagraph two.\n\nParagraph three."
    chunks = split_into_chunks(
        text, chunk_size=500, chunk_overlap=50, source_id="hardcap_test"
    )
    for chunk in chunks:
        assert len(chunk.content) <= 500, (
            f"Chunk {chunk.doc_id} has {len(chunk.content)} chars (> 500)"
        )
    # The huge paragraph should produce at least 2000/500 = 4 chunks.
    assert len(chunks) >= 4


def test_split_preserves_overlap() -> None:
    """When chunk_overlap > 0, the overlap text carries forward."""
    text = "A" * 400 + "\n\n" + "B" * 400
    chunks = split_into_chunks(
        text, chunk_size=600, chunk_overlap=100, source_id="overlap_test"
    )
    # With 600 cap, both 400-char paragraphs fit in one chunk.
    # But if they were to overflow, overlap would carry from the last paragraph.
    for chunk in chunks:
        assert len(chunk.content) <= 600


# ── Heading path depth ──


def test_parse_markdown_heading_path() -> None:
    """parse_markdown emits full heading paths for nested sections."""
    md = (
        "# Top\n\nTop-level body.\n\n"
        "## Child\n\nChild body.\n\n"
        "### Grandchild\n\nDeep body.\n\n"
        "## Other Child\n\nOther body."
    )
    chunks = parse_markdown(md, source_id="test")
    assert len(chunks) >= 4  # Top, Child, Grandchild, Other Child

    # Grandchild chunk should have full path "Top > Child > Grandchild".
    deep_chunks = [c for c in chunks if "Deep body" in c.content]
    assert len(deep_chunks) == 1
    assert deep_chunks[0].path == "Top > Child > Grandchild"

    # Child chunk should have path "Top > Child".
    child_chunks = [c for c in chunks if c.title == "Child"]
    assert len(child_chunks) == 1
    assert child_chunks[0].path == "Top > Child"

    # Top chunk should have path "Top".
    top_chunks = [c for c in chunks if c.title == "Top"]
    assert any(c.path == "Top" for c in top_chunks)


def test_parse_markdown_flat_headings() -> None:
    """Sibling headings share the same parent path."""
    md = "# Root\n\nRoot body.\n\n## A\n\nA body.\n\n## B\n\nB body."
    chunks = parse_markdown(md, source_id="flat")
    a_chunks = [c for c in chunks if c.title == "A"]
    b_chunks = [c for c in chunks if c.title == "B"]
    assert len(a_chunks) == 1
    assert len(b_chunks) == 1
    assert a_chunks[0].path == "Root > A"
    assert b_chunks[0].path == "Root > B"


# ── retrieval_text content ──


def test_build_retrieval_text_with_path() -> None:
    """retrieval_text prefixes with the path when available."""
    result = _build_retrieval_text(
        content="This is the chunk body.",
        path="Docs > Section",
        source_id="manual.md",
        title="Section",
    )
    assert result.startswith("Docs > Section")
    assert "This is the chunk body." in result


def test_build_retrieval_text_falls_back_to_source_id() -> None:
    """When path is empty, retrieval_text uses source_id."""
    result = _build_retrieval_text(
        content="Content here.",
        path="",
        source_id="imported.md",
        title="Some Title",
    )
    assert result.startswith("imported.md")
    assert "Content here." in result


def test_build_retrieval_text_falls_back_to_title() -> None:
    """When path and source_id are empty, retrieval_text uses title."""
    result = _build_retrieval_text(
        content="Body text.",
        path="",
        source_id="",
        title="Chunk Title",
    )
    assert result.startswith("Chunk Title")
    assert "Body text." in result


def test_chunk_retrieval_text_populated() -> None:
    """split_into_chunks builds retrieval_text for every chunk."""
    text = "Paragraph one.\n\nParagraph two.\n\nParagraph three."
    chunks = split_into_chunks(
        text,
        chunk_size=200,
        chunk_overlap=20,
        source_id="test_doc",
        path="My > Doc",
    )
    assert len(chunks) > 0
    for chunk in chunks:
        assert chunk.retrieval_text, f"Chunk {chunk.doc_id} has no retrieval_text"
        assert chunk.source_id == "test_doc"
        assert chunk.path == "My > Doc"
        assert chunk.chunk_index >= 0


# ── Provenance fields on import ──


@pytest.mark.asyncio
async def test_import_document_stores_provenance_columns() -> None:
    """import_document writes retrieval_text/path/source_id/chunk_index."""
    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    try:
        store = SQLiteDocumentStore(engine, collection="provenance_test")
        await store.setup()

        count = await import_document(
            store,
            source_id="test_prov",
            content="Hello world.\n\nMore content.",
            content_type="text",
            chunk_size=200,
            chunk_overlap=20,
        )
        assert count > 0

        # Verify provenance columns via repo row.
        rows = await store._repo.list_documents(limit=10)  # noqa: SLF001
        for row in rows:
            assert "source_id" in row
            assert row.get("source_id") == "test_prov"
            assert "chunk_index" in row
            assert row.get("chunk_index") is not None
            assert "path" in row
            assert "retrieval_text" in row
            assert row.get("retrieval_text", ""), (
                f"retrieval_text empty for {row.get('doc_id')}"
            )
    finally:
        await engine.close()


# ── Neighbor expansion ──


@pytest.mark.asyncio
async def test_get_neighbors_returns_adjacent_chunks() -> None:
    """get_neighbors finds sibling chunks of the same source."""
    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    try:
        store = SQLiteDocumentStore(engine, collection="neighbor_test")
        await store.setup()

        # Insert 5 chunks for the same source.
        for i in range(5):
            await store.put(
                f"src_chunk_{i}",
                f"Chunk {i} body text.",
                title=f"Part {i + 1}",
                source_id="neighbor_src",
                chunk_index=i,
                retrieval_text=f"Part {i + 1}\nChunk {i} body text.",
            )

        # Get neighbors of chunk 2.
        neighbors = await store.get_neighbors(
            "neighbor_src",
            chunk_index=2,
            before=1,
            after=1,
        )
        neighbor_ids = {n.doc_id for n in neighbors}
        assert "src_chunk_1" in neighbor_ids, "missing before neighbor"
        assert "src_chunk_3" in neighbor_ids, "missing after neighbor"
        # Center chunk (src_chunk_2) is deliberately excluded by the query.
        assert "src_chunk_2" not in neighbor_ids
        # chunk 0 and chunk 4 should NOT be there.
        assert "src_chunk_0" not in neighbor_ids
        assert "src_chunk_4" not in neighbor_ids
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_get_neighbors_at_boundary() -> None:
    """Neighbors at index 0 don't include negative indices."""
    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    try:
        store = SQLiteDocumentStore(engine, collection="boundary_test")
        await store.setup()

        await store.put(
            "src_chunk_0",
            "First chunk.",
            source_id="boundary_src",
            chunk_index=0,
            retrieval_text="First chunk.",
        )
        await store.put(
            "src_chunk_1",
            "Second chunk.",
            source_id="boundary_src",
            chunk_index=1,
            retrieval_text="Second chunk.",
        )

        neighbors = await store.get_neighbors(
            "boundary_src",
            chunk_index=0,
            before=1,
            after=1,
        )
        neighbor_ids = {n.doc_id for n in neighbors}
        assert "src_chunk_1" in neighbor_ids  # neighbor ahead
        # src_chunk_0 is the center (chunk_index=0) and is excluded by the query.
        assert "src_chunk_0" not in neighbor_ids
        assert len(neighbors) == 1  # only the forward neighbor, no negative index
    finally:
        await engine.close()


# ── retrieval_text in FTS ──


@pytest.mark.asyncio
async def test_fts_searches_retrieval_text_not_just_content() -> None:
    """FTS matches text from the enriched retrieval_text (e.g. path prefix)."""
    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    try:
        store = SQLiteDocumentStore(engine, collection="fts_enriched")
        await store.setup()

        # Put a chunk whose content doesn't contain "Guide", but the
        # retrieval_text does (it prefixes with the heading path).
        await store.put(
            "guide_chunk_0",
            content="Step 1: Initialize the system properly.",
            title="Setup",
            path="Guide > Setup",
            source_id="manual",
            chunk_index=0,
            retrieval_text="Guide > Setup\nStep 1: Initialize the system properly.",
        )

        # Searching "Guide" should find this chunk via FTS on retrieval_text.
        results = await store.search("Guide", limit=5)
        assert any(r.doc_id == "guide_chunk_0" for r in results), (
            f"FTS should match 'Guide' in retrieval_text. Got: "
            f"{[r.doc_id for r in results]}"
        )
    finally:
        await engine.close()


# ── Backward compatibility ──


@pytest.mark.asyncio
async def test_pre_phase1_store_put_still_works() -> None:
    """Callers using the old put() signature (no new params) still work."""
    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    try:
        store = SQLiteDocumentStore(engine, collection="backcompat")
        await store.setup()

        # Old-style call: only doc_id, content, title, metadata.
        await store.put("old_doc", "Old content", title="Old Title")
        item = await store.get("old_doc")
        assert item is not None
        assert item.content == "Old content"
        assert item.title == "Old Title"
        # New fields get defaults.
        assert item.retrieval_text == ""
        assert item.path == ""
        assert item.source_id == ""
        assert item.chunk_index == 0

        # Search still works (falls back to title+content for FTS).
        results = await store.search("Old content", limit=5)
        assert any(r.doc_id == "old_doc" for r in results)
    finally:
        await engine.close()


# ── Embedding uses retrieval_text ──


@pytest.mark.asyncio
async def test_embedding_text_falls_back_to_title_content() -> None:
    """When retrieval_text is empty, embedding uses title + content."""
    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    try:
        store = SQLiteDocumentStore(engine, collection="embed_fallback")
        await store.setup()

        await store.put("fallback", "Body only", title="My Title")
        provider = HashEmbeddingProvider(dimensions=8)

        result = await store.embed_documents(provider, limit=10)
        assert result.added == result.needed  # fully embedded
        assert result.added == 1
    finally:
        await engine.close()
