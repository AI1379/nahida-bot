"""Domain models for the generic document store."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(slots=True, frozen=True)
class DocumentItem:
    """A stored document chunk in a collection.

    ``content`` is the raw, display/citation text. ``retrieval_text`` is the
    enriched text (source title + heading ``path`` + content) used for FTS and
    embeddings; empty for pre-Phase-1 rows, in which case callers fall back to
    title + content. ``source_id`` + ``chunk_index`` locate the chunk within its
    source document and enable neighbor expansion.
    """

    doc_id: str
    title: str
    content: str
    status: str = "active"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    retrieval_text: str = ""
    path: str = ""
    source_id: str = ""
    chunk_index: int = 0


@dataclass(slots=True, frozen=True)
class DocumentEmbedding:
    """Persisted embedding for a document."""

    embedding_id: str
    doc_id: str
    provider_id: str
    model: str
    dimensions: int
    content_hash: str
    embedding: list[float]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(slots=True, frozen=True)
class Provenance:
    """Stable source provenance for a retrieved chunk (knowledge-base.md §5.1).

    Dual-mode by design: the document side (``source_type`` / ``source_id`` /
    ``collection`` / ``path`` / ``page`` / ``version``) is populated by KB now;
    the conversation side (``turn_id`` / ``session_id`` / ``person_id`` /
    ``account_key``) is reserved for when memory turns become ContextNodes
    (Phase 3). Only one side is populated per record.
    """

    source_type: str = "document"
    source_id: str = ""
    collection: str = ""
    path: str = ""
    page: str = ""
    version: str = ""
    turn_id: str = ""
    session_id: str = ""
    person_id: str = ""
    account_key: str = ""


@dataclass(slots=True, frozen=True)
class SearchResult:
    """A document search result with relevance score and provenance."""

    doc_id: str
    title: str
    content: str
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    path: str = ""
    source_id: str = ""
    chunk_index: int = 0
