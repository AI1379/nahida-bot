"""Document ingestion — parsing, chunking, and import pipeline.

Handles splitting documents into search-friendly chunks and storing them
in a ``DocumentStore`` collection.

Phase 1 (knowledge-base.md): chunk_size is a real upper bound (over-long
paragraphs are sub-split by sentence, then character window), Markdown parsing
keeps the **full heading path** per chunk, and each chunk carries an enriched
``retrieval_text`` (source + heading path + content) used for FTS and embeddings
— separate from the raw ``content`` used for display/citation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from nahida_bot.agent.storage.document_store import DocumentStore

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class Chunk:
    """A single document chunk ready for storage.

    ``content`` is the raw display/citation text. ``retrieval_text`` is the
    enriched text (source + heading path + content) indexed for FTS and
    embeddings. ``path`` is the full heading trail. ``source_id`` /
    ``chunk_index`` locate the chunk within its source and enable neighbor
    expansion.
    """

    doc_id: str
    title: str
    content: str
    retrieval_text: str
    source_id: str
    chunk_index: int
    path: str = ""
    parent_id: str = ""
    root_id: str = ""
    node_type: str = "passage"
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def _split_paragraphs(text: str) -> list[str]:
    """Split text into non-empty paragraphs."""
    return [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]


def _make_chunk_id(source_id: str, index: int) -> str:
    """Build a deterministic chunk ID."""
    # Sanitize source_id to be filesystem / doc-id friendly.
    safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", source_id)
    return f"{safe}_chunk_{index}"


def _build_retrieval_text(
    *,
    content: str,
    path: str = "",
    source_id: str = "",
    title: str = "",
) -> str:
    """Build the enriched text used for FTS and embedding.

    Combines every non-empty locator — ``path`` (heading trail), ``source_id``
    (filename / import name), and ``title`` (section heading) — into a
    newline-separated prefix above the chunk content.  Indexing this lets a
    query that matches only a parent heading, filename, or section name still
    surface the child chunk, which the old "pick one" fallback missed
    (knowledge-base.md Phase 1 §12 第 3 项).
    """
    # Don't repeat the leaf heading: section nodes pass both path (which ends
    # with the heading) and title=heading, which would triple-count the term
    # in BM25/embeddings. source_id/title are only added if they aren't the
    # path's last segment.
    path_leaf = path.rsplit(">", 1)[-1].strip() if path else ""
    lines: list[str] = []
    if path.strip():
        lines.append(path.strip())
    for candidate in (source_id, title):
        val = candidate.strip()
        if val and val != path_leaf:
            lines.append(val)
    if lines:
        return "\n".join(lines) + "\n" + content
    return content


# Sentence boundaries for the hard-cap sub-split.
# Latin terminators (.!?) require trailing whitespace so "3.14" stays intact.
# CJK terminators (。！？) split at zero width — CJK text has no space after them.
_SENTENCE_SPLIT = re.compile(r"(?:(?<=[.!?])\s+|(?<=[。！？]))")


def _split_long_paragraph(paragraph: str, chunk_size: int) -> list[str]:
    """Split a paragraph longer than ``chunk_size`` into pieces at or below it.

    Splits on sentence boundaries first, then by a character window for any
    single sentence still over the cap, so no returned piece exceeds
    ``chunk_size``. This is what makes ``chunk_size`` a real upper bound
    (fixes knowledge-base.md §3.3).
    """
    sentences = [s for s in _SENTENCE_SPLIT.split(paragraph) if s]
    pieces: list[str] = []
    for sentence in sentences:
        if len(sentence) <= chunk_size:
            pieces.append(sentence)
            continue
        for i in range(0, len(sentence), chunk_size):
            pieces.append(sentence[i : i + chunk_size])
    return pieces or [paragraph]


def split_into_chunks(
    text: str,
    *,
    source_id: str,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
    title_prefix: str = "",
    path: str = "",
    chunk_id_prefix: str = "",
    parent_id: str = "",
    root_id: str = "",
    node_type: str = "passage",
    index_offset: int = 0,
) -> list[Chunk]:
    """Split raw text into ``Chunk`` objects, each at most ~``chunk_size`` chars.

    The algorithm works on paragraph boundaries:

    1. Split text into paragraphs (double-newline).
    2. **Hard cap**: any paragraph longer than ``chunk_size`` is sub-split by
       sentence (then character window) so no chunk exceeds the cap.
    3. Accumulate bounded paragraphs into chunks up to ``chunk_size``.
    4. On overflow, emit a chunk and carry forward up to ``chunk_overlap`` chars.

    ``path`` is stamped onto every chunk (the heading trail from the caller);
    ``chunk_id_prefix`` overrides the doc-id base (used by Markdown to keep
    duplicate headings' chunk ids unique). ``retrieval_text`` is built per chunk.
    """
    if not text or not text.strip():
        return []

    raw_paragraphs = _split_paragraphs(text)
    # Enforce the hard cap: break any over-long paragraph into bounded pieces.
    paragraphs: list[str] = []
    for para in raw_paragraphs:
        if len(para) <= chunk_size:
            paragraphs.append(para)
        else:
            paragraphs.extend(_split_long_paragraph(para, chunk_size))
    if not paragraphs:
        return []

    id_base = chunk_id_prefix or source_id
    chunks: list[Chunk] = []
    current_parts: list[str] = []
    current_len = 0
    index = index_offset

    def emit(parts: list[str], idx: int, *, simplify_title: bool) -> None:
        chunk_text = "\n\n".join(parts)
        # Part number is local to this call (relative to index_offset) so a
        # section whose passages start at offset 5 still shows "part 1, 2, ...".
        part = idx - index_offset
        title = (
            title_prefix
            if (simplify_title and title_prefix)
            else (
                f"{title_prefix} (part {part + 1})"
                if title_prefix
                else f"Part {part + 1}"
            )
        )
        chunks.append(
            Chunk(
                doc_id=_make_chunk_id(id_base, idx),
                title=title,
                content=chunk_text,
                retrieval_text=_build_retrieval_text(
                    content=chunk_text,
                    path=path,
                    source_id=source_id,
                    title=title_prefix,
                ),
                source_id=source_id,
                chunk_index=idx,
                path=path,
                parent_id=parent_id,
                root_id=root_id,
                node_type=node_type,
            )
        )

    for para in paragraphs:
        # All paragraphs are now <= chunk_size, so an empty buffer never
        # produces an over-sized chunk (the old §3.3 bug).
        if not current_parts:
            current_parts.append(para)
            current_len = len(para)
            continue

        if current_len + 1 + len(para) > chunk_size:
            emit(current_parts, index, simplify_title=False)
            index += 1
            # Carry forward overlap from the end of the last paragraph.
            if chunk_overlap > 0 and len(current_parts[-1]) > chunk_overlap:
                overlap_text = current_parts[-1][-chunk_overlap:]
                # If overlap + para still exceeds the cap (both near max size),
                # drop the overlap and start fresh so the hard cap is preserved.
                if len(overlap_text) + 1 + len(para) > chunk_size:
                    current_parts = [para]
                    current_len = len(para)
                else:
                    current_parts = [overlap_text, para]
                    current_len = len(overlap_text) + 1 + len(para)
            else:
                current_parts = [para]
                current_len = len(para)
        else:
            current_parts.append(para)
            current_len += 1 + len(para)

    if current_parts:
        # If this is the only chunk, simplify the "(part 1)" title away.
        single = not chunks
        emit(current_parts, index, simplify_title=single)

    return chunks


# ---------------------------------------------------------------------------
# Markdown parsing
# ---------------------------------------------------------------------------

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def parse_markdown(
    text: str,
    *,
    source_id: str,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
) -> list[Chunk]:
    """Parse a Markdown document into hierarchical chunks.

    Returns a mixed list of **section nodes** (``node_type='document'`` or
    ``'section'``) and **passage chunks** (``node_type='passage'``). Section
    nodes carry the heading title as ``content`` and link to their parent via
    ``parent_id``; passage chunks link to the nearest ancestor section. Every
    node within one source document shares the same ``root_id``.

    Maintains a heading **stack** while walking the document so each node's
    ``path`` is the joined ancestor headings (e.g.
    "原神角色资料 > 阿贝多 > 角色故事 5").
    """
    root_doc_id = _make_chunk_id(source_id, -1)  # unique root id

    # ── No headings: flat document with a single root section ──
    matches = list(_MD_HEADING.finditer(text))
    if not matches:
        body = text.strip()
        if not body:
            return []
        root = Chunk(
            doc_id=root_doc_id,
            title=source_id,
            content=source_id,
            retrieval_text=_build_retrieval_text(
                content=source_id, source_id=source_id, title=source_id
            ),
            source_id=source_id,
            chunk_index=0,
            path="",
            parent_id="",
            root_id=root_doc_id,
            node_type="document",
        )
        chunks: list[Chunk] = [root]
        passage_chunks = split_into_chunks(
            body,
            source_id=source_id,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            title_prefix=source_id,
            path="",
            parent_id=root_doc_id,
            root_id=root_doc_id,
            index_offset=1,
        )
        chunks.extend(passage_chunks)
        return chunks

    # ── Heading stack: build section nodes + passage chunks ──
    all_chunks: list[Chunk] = []
    # Monotonic per-source chunk index so (source_id, chunk_index) is unique —
    # neighbor expansion keys on that pair and must not return nodes from other
    # sections (previously the root node, every section node, and every first
    # passage all shared chunk_index=0 under one source_id).
    seq = 0

    def _doc_node() -> Chunk:
        return Chunk(
            doc_id=root_doc_id,
            title=source_id,
            content=source_id,
            retrieval_text=_build_retrieval_text(
                content=source_id, source_id=source_id, title=source_id
            ),
            source_id=source_id,
            chunk_index=seq,
            path="",
            parent_id="",
            root_id=root_doc_id,
            node_type="document",
        )

    # Root document node (lead section), always present.
    all_chunks.append(_doc_node())
    seq += 1

    # Pre-content before the first heading → root document's lead passages.
    pre = text[: matches[0].start()].strip()
    if pre:
        passage_chunks = split_into_chunks(
            pre,
            source_id=source_id,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            title_prefix=source_id,
            path="",
            parent_id=root_doc_id,
            root_id=root_doc_id,
            index_offset=seq,
        )
        seq += len(passage_chunks)
        all_chunks.extend(passage_chunks)

    stack: list[tuple[int, str, str]] = []  # (level, heading_title, section_doc_id)
    for i, m in enumerate(matches):
        level = len(m.group(1))
        heading_title = m.group(2).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        current_path = " > ".join(title for _lvl, title, _sid in stack)
        if current_path:
            current_path += " > " + heading_title
        else:
            current_path = heading_title

        # Parent is the nearest ancestor section, or the document root.
        section_parent_id = stack[-1][2] if stack else root_doc_id
        section_doc_id = _make_chunk_id(
            f"{source_id}__sec_{_safe_section_name(heading_title)}", i
        )
        stack.append((level, heading_title, section_doc_id))

        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()

        # Section node: heading + first ~500 chars of immediate body.
        section = Chunk(
            doc_id=section_doc_id,
            title=heading_title,
            content=heading_title,
            retrieval_text=_build_retrieval_text(
                content=heading_title,
                path=current_path,
                source_id=source_id,
                title=heading_title,
            ),
            source_id=source_id,
            chunk_index=seq,
            path=current_path,
            parent_id=section_parent_id,
            root_id=root_doc_id,
            node_type="section",
        )
        all_chunks.append(section)
        seq += 1

        if body:
            section_prefix = f"{source_id}__{i}_{_safe_section_name(heading_title)}"
            passage_chunks = split_into_chunks(
                body,
                source_id=source_id,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                title_prefix=heading_title,
                path=current_path,
                chunk_id_prefix=section_prefix,
                parent_id=section_doc_id,
                root_id=root_doc_id,
                index_offset=seq,
            )
            seq += len(passage_chunks)
            all_chunks.extend(passage_chunks)

    return all_chunks


def _safe_section_name(name: str) -> str:
    """Convert a heading to a filesystem-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    return slug.strip("_")[:40]


# ---------------------------------------------------------------------------
# Import pipeline
# ---------------------------------------------------------------------------


async def import_document(
    store: DocumentStore,
    source_id: str,
    content: str,
    *,
    content_type: str = "text",
    chunk_size: int = 500,
    chunk_overlap: int = 50,
    extra_metadata: dict | None = None,
) -> int:
    """Parse, chunk, and store a document into a collection.

    Parameters
    ----------
    store:
        Target ``DocumentStore`` collection.
    source_id:
        Logical identifier for the source (filename, title, etc.).
    content:
        Raw document content.
    content_type:
        ``"markdown"`` or ``"text"`` — affects parsing strategy.
    chunk_size:
        Max characters per chunk (now a real upper bound).
    chunk_overlap:
        Overlap characters between chunks.
    extra_metadata:
        Additional metadata to attach to every chunk.

    Returns
    -------
    Number of chunks created.
    """
    if content_type == "markdown":
        chunks = parse_markdown(
            content,
            source_id=source_id,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
    else:
        # Plain text: create a document root + passage chunks (same pattern as
        # Markdown no-headings), so context_read works for text imports too.
        body = content.strip()
        if not body:
            logger.warning("kb.import_empty", source_id=source_id)
            return 0
        root_doc_id = _make_chunk_id(source_id, -1)
        root = Chunk(
            doc_id=root_doc_id,
            title=source_id,
            content=source_id,
            retrieval_text=_build_retrieval_text(
                content=source_id, source_id=source_id, title=source_id
            ),
            source_id=source_id,
            chunk_index=0,
            path="",
            parent_id="",
            root_id=root_doc_id,
            node_type="document",
        )
        chunks = [root]
        passage_chunks = split_into_chunks(
            body,
            source_id=source_id,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            title_prefix=source_id,
            path="",
            parent_id=root_doc_id,
            root_id=root_doc_id,
        )
        chunks.extend(passage_chunks)

    if not chunks:
        logger.warning("kb.import_empty", source_id=source_id)
        return 0

    for chunk in chunks:
        # Keep source_id / chunk_index / path in metadata too, for any consumer
        # that still reads provenance from metadata rather than the columns.
        metadata = {
            "source_id": chunk.source_id,
            "chunk_index": chunk.chunk_index,
            "path": chunk.path,
            **(extra_metadata or {}),
        }
        await store.put(
            chunk.doc_id,
            chunk.content,
            title=chunk.title,
            metadata=metadata,
            retrieval_text=chunk.retrieval_text,
            path=chunk.path,
            source_id=chunk.source_id,
            chunk_index=chunk.chunk_index,
            parent_id=chunk.parent_id,
            root_id=chunk.root_id,
            node_type=chunk.node_type,
        )

    logger.info(
        "kb.imported",
        source_id=source_id,
        chunks=len(chunks),
        content_type=content_type,
    )
    return len(chunks)
