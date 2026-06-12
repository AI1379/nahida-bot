"""Domain models for the generic document store."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(slots=True, frozen=True)
class DocumentItem:
    """A stored document in a collection."""

    doc_id: str
    title: str
    content: str
    status: str = "active"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


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
class SearchResult:
    """A document search result with relevance score."""

    doc_id: str
    title: str
    content: str
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
