"""Document ingestion — parsing, chunking, and import pipeline.

Handles splitting documents into search-friendly chunks and storing them
in a ``DocumentStore`` collection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from nahida_bot.agent.storage.document_store import DocumentStore

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class Chunk:
    """A single document chunk ready for storage."""

    doc_id: str
    title: str
    content: str
    metadata: dict


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


def split_into_chunks(
    text: str,
    *,
    source_id: str,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
    title_prefix: str = "",
) -> list[Chunk]:
    """Split raw text into ``Chunk`` objects.

    The algorithm works on paragraph boundaries:

    1. Split text into paragraphs (double-newline).
    2. Accumulate paragraphs into chunks up to *chunk_size* characters.
    3. When the accumulated length exceeds *chunk_size*, emit a chunk.
    4. For the next chunk, carry forward up to *chunk_overlap* characters
       of the last paragraph to maintain context continuity.

    If a single paragraph is longer than *chunk_size*, it is emitted as
    its own chunk (no further splitting).
    """
    if not text or not text.strip():
        return []

    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        return []

    chunks: list[Chunk] = []
    current_parts: list[str] = []
    current_len = 0
    index = 0

    for para in paragraphs:
        # If the buffer is empty, always add the paragraph.
        if not current_parts:
            current_parts.append(para)
            current_len = len(para)
            continue

        # Would adding this paragraph exceed the limit?
        if current_len + 1 + len(para) > chunk_size and current_parts:
            # Emit current buffer as a chunk.
            chunk_text = "\n\n".join(current_parts)
            chunks.append(
                Chunk(
                    doc_id=_make_chunk_id(source_id, index),
                    title=f"{title_prefix} (part {index + 1})"
                    if title_prefix
                    else f"Part {index + 1}",
                    content=chunk_text,
                    metadata={
                        "source_id": source_id,
                        "chunk_index": index,
                    },
                )
            )
            index += 1

            # Carry forward overlap from the end of the last paragraph.
            if chunk_overlap > 0 and len(current_parts[-1]) > chunk_overlap:
                overlap_text = current_parts[-1][-chunk_overlap:]
                current_parts = [overlap_text, para]
                current_len = len(overlap_text) + 1 + len(para)
            else:
                current_parts = [para]
                current_len = len(para)
        else:
            current_parts.append(para)
            current_len += 1 + len(para)

    # Emit remaining buffer.
    if current_parts:
        chunk_text = "\n\n".join(current_parts)
        chunks.append(
            Chunk(
                doc_id=_make_chunk_id(source_id, index),
                title=f"{title_prefix} (part {index + 1})"
                if title_prefix
                else f"Part {index + 1}",
                content=chunk_text,
                metadata={
                    "source_id": source_id,
                    "chunk_index": index,
                },
            )
        )

    # If there's only one chunk, simplify the title.
    if len(chunks) == 1 and title_prefix:
        chunks[0] = Chunk(
            doc_id=chunks[0].doc_id,
            title=title_prefix,
            content=chunks[0].content,
            metadata={**chunks[0].metadata, "chunk_index": 0},
        )

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
    """Parse a Markdown document into chunks.

    Splits on ``## `` (or deeper) headings.  Each section becomes one or
    more chunks depending on size.
    """
    # Find heading positions.
    sections: list[tuple[str, str]] = []  # (heading_title, section_body)

    last_end = 0
    last_title = ""

    for m in _MD_HEADING.finditer(text):
        # Content before this heading belongs to the previous section.
        body = text[last_end : m.start()].strip()
        if body or last_title:
            sections.append((last_title, body))
        last_title = m.group(2).strip()
        last_end = m.end()

    # Trailing content after the last heading.
    trailing = text[last_end:].strip()
    if trailing or last_title:
        sections.append((last_title, trailing))

    # If no headings were found, treat the whole document as one section.
    if not sections:
        sections = [("", text.strip())]

    all_chunks: list[Chunk] = []
    for section_index, (heading_title, body) in enumerate(sections):
        if not body:
            continue
        prefix = heading_title if heading_title else source_id
        section_id = (
            f"{source_id}__{section_index}_{_safe_section_name(heading_title)}"
            if heading_title
            else source_id
        )
        chunks = split_into_chunks(
            body,
            source_id=section_id,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            title_prefix=prefix,
        )
        all_chunks.extend(chunks)

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
        Max characters per chunk.
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
        chunks = split_into_chunks(
            content,
            source_id=source_id,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            title_prefix=source_id,
        )

    if not chunks:
        logger.warning("kb.import_empty", source_id=source_id)
        return 0

    for chunk in chunks:
        metadata = {**(extra_metadata or {}), **chunk.metadata}
        await store.put(
            chunk.doc_id,
            chunk.content,
            title=chunk.title,
            metadata=metadata,
        )

    logger.info(
        "kb.imported",
        source_id=source_id,
        chunks=len(chunks),
        content_type=content_type,
    )
    return len(chunks)
