"""Knowledge Base plugin configuration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class KBRetrievalConfig(BaseModel):
    """Retrieval settings for the Knowledge Base plugin."""

    model_config = ConfigDict(frozen=True, extra="allow")

    fts_enabled: bool = True
    vector_enabled: bool = False
    hybrid_enabled: bool = True
    vector_backend: Literal["json", "sqlite-vec", "none"] = "json"
    expand_neighbors: bool = Field(
        default=False,
        description=(
            "When true, append ±1 adjacent chunks for the top results "
            "(same source document, adjacent chunk_index)."
        ),
    )
    expand_neighbors_top_k: int = Field(
        default=3,
        description="Number of top results to expand with neighboring chunks.",
    )


class KBEmbeddingConfig(BaseModel):
    """Embedding settings for the Knowledge Base plugin."""

    model_config = ConfigDict(frozen=True, extra="allow")

    enabled: bool = False
    model: str = ""
    dimensions: int = Field(default=0, ge=0)
    batch_size: int = Field(default=16, ge=1)
    embed_after_import: bool = True


class KBConfig(BaseModel):
    """Configuration for the Knowledge Base plugin.

    Parsed from the ``config`` block in ``plugin.yaml`` and merged
    with any top-level ``knowledge_base:`` key in ``config.yaml``.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    default_chunk_size: int = Field(
        default=500,
        ge=1,
        description="Maximum characters per document chunk.",
    )
    default_chunk_overlap: int = Field(
        default=50,
        description="Overlap characters between consecutive chunks.",
    )
    max_search_results: int = Field(
        default=5,
        description="Maximum documents returned by kb_search.",
    )
    retrieval: KBRetrievalConfig = KBRetrievalConfig()
    embedding: KBEmbeddingConfig = KBEmbeddingConfig()


def parse_kb_config(raw: dict | None) -> KBConfig:
    """Parse a raw config dict into a validated ``KBConfig``."""
    return KBConfig(**(raw or {}))
