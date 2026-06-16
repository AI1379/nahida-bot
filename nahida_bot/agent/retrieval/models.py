"""Common retrieval request and result models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

RetrievalSourceType = Literal["memory", "knowledge_base"]
RetrievalMode = Literal["fts", "vector", "hybrid", "none"]


@dataclass(frozen=True, slots=True)
class RetrievalScope:
    """A logical retrieval scope."""

    scope_type: str
    scope_id: str


@dataclass(frozen=True, slots=True)
class RetrievalRequest:
    """Common retrieval request for memory and KB backends."""

    query: str
    source_type: RetrievalSourceType
    limit: int = 10
    scope: RetrievalScope | None = None
    collection: str = ""
    fts_enabled: bool = True
    vector_enabled: bool = False
    hybrid_enabled: bool = True
    allow_global_fallback: bool = False


@dataclass(frozen=True, slots=True)
class RetrievalProvenance:
    """Stable provenance fields shared by memory and KB results."""

    source_type: RetrievalSourceType
    source_id: str
    collection: str = ""
    scope_type: str = ""
    scope_id: str = ""
    kind: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """Common retrieval result returned by adapters."""

    result_id: str
    title: str
    text: str
    source_type: RetrievalSourceType
    score: float = 0.0
    mode: RetrievalMode = "none"
    provenance: RetrievalProvenance | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    raw: Any | None = field(default=None, compare=False, repr=False)
