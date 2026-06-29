"""Memory data models: ConversationTurn and MemoryRecord."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

# Canonical sensitivity domain (Piece A). The soft-scope retrieval filter
# matches ``sensitivity='public'`` at the SQL layer, so values MUST be the
# exact lowercase canonical strings — a typo or different casing would either
# leak (treated as non-public when it should be public) or silently under-recall.
# Use the normalizers below at every write/read boundary.
Sensitivity = Literal["public", "private", "secret_like"]
SensitivitySource = Literal["default", "dream", "explicit"]

_SENSITIVITY_VALUES = frozenset({"public", "private", "secret_like"})
_SENSITIVITY_SOURCE_VALUES = frozenset({"default", "dream", "explicit"})


def normalize_sensitivity(value: object) -> Sensitivity:
    """Coerce an arbitrary value to a canonical ``Sensitivity``.

    Falls back to the soft ``public`` baseline on anything unrecognized
    (typos, wrong casing, None) rather than rejecting the write — the
    soft-scope model is fail-open on the public baseline, never fail-closed
    into a restricted tag.
    """
    text = str(value).strip().casefold()
    return text if text in _SENSITIVITY_VALUES else "public"  # type: ignore[return-value]


def normalize_sensitivity_source(value: object) -> SensitivitySource:
    """Coerce an arbitrary value to a canonical ``SensitivitySource``.

    Falls back to ``default`` on anything unrecognized.
    """
    text = str(value).strip().casefold()
    return text if text in _SENSITIVITY_SOURCE_VALUES else "default"  # type: ignore[return-value]


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
    sensitivity: Sensitivity = "public"
    # Provenance of the sensitivity value: "default" (system default, soft /
    # recallable), "dream" (consolidation classified it restricted), "explicit"
    # (chat partner requested restriction). The soft-scope retrieval filter
    # excludes restricted items (private/secret_like) from cross-scope recall;
    # only dream/explicit items are truly restricted, so the backfill script
    # reinterprets legacy "default private" rows as public.
    sensitivity_source: SensitivitySource = "default"
    source: str = "plugin"
    evidence: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    score: float = 0.0
    parent_id: str = ""
    root_id: str = ""
    node_type: str = "leaf"
    path: str = ""
    source_id: str = ""


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
