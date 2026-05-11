"""Memory data models: ConversationTurn and MemoryRecord."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(slots=True, frozen=True)
class ConversationTurn:
    """A single turn in a conversation, used for memory persistence."""

    role: str
    content: str
    source: str = ""
    metadata: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(slots=True, frozen=True)
class MemoryRecord:
    """A stored memory entry retrieved from the memory store."""

    turn_id: int
    session_id: str
    turn: ConversationTurn
    keywords: list[str] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class MemoryItem:
    """A durable structured memory item."""

    item_id: str
    scope_type: str
    scope_id: str
    kind: str
    title: str
    content: str
    status: str = "active"
    confidence: float = 1.0
    importance: float = 0.5
    sensitivity: str = "private"
    source: str = "plugin"
    evidence: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    score: float = 0.0


@dataclass(slots=True, frozen=True)
class MemoryEmbedding:
    """Persisted embedding for one durable memory item."""

    embedding_id: str
    item_id: str
    provider_id: str
    model: str
    dimensions: int
    content_hash: str
    embedding: list[float]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(slots=True, frozen=True)
class MemoryCandidate:
    """Candidate memory extracted during consolidation."""

    candidate_id: str
    scope_type: str
    scope_id: str
    kind: str
    title: str
    content: str
    status: str = "pending"
    confidence: float = 0.5
    evidence: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(slots=True, frozen=True)
class SessionSummary:
    """Summary of a session for listing purposes."""

    session_id: str
    workspace_id: str | None
    created_at: str
    last_active_at: str
    turn_count: int
    metadata: dict[str, Any] = field(default_factory=dict)
