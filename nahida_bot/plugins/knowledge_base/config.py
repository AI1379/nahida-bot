"""Knowledge Base plugin configuration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class KBDreamPromotionConfig(BaseModel):
    """Dreaming→KB promotion (A3, dreaming-to-kb.md) — default OFF.

    The DreamPromoter scans global-scope durable memory items and imports the
    ones passing this gate into a dedicated KB collection as knowledge nodes.
    Every threshold lives here on purpose (owner decision 2026-08-25): code
    carries defaults only, operators tune the config file.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    enabled: bool = False
    collection: str = "dreams"
    min_confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    daily_limit: int = Field(default=2, ge=0)
    kinds: list[str] = Field(default_factory=lambda: ["fact", "procedure", "decision"])
    scan_limit: int = Field(
        default=200, ge=1, description="Max memory items scanned per pass."
    )
    interval_seconds: int = Field(
        default=3600, ge=60, description="Delay between promoter passes."
    )


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
    storage_dir: str = Field(
        default="",
        description=(
            "Directory for per-collection KB database files (issue #26 split "
            "layout). Each collection becomes {storage_dir}/{name}.db holding "
            "its docs, FTS, embedding JSON, and vec index; the main db keeps "
            "only bot-core data. Empty (default) keeps the legacy layout with "
            "KB tables in the main db."
        ),
    )
    retrieval: KBRetrievalConfig = KBRetrievalConfig()
    embedding: KBEmbeddingConfig = KBEmbeddingConfig()
    dream_promotion: KBDreamPromotionConfig = KBDreamPromotionConfig()


def parse_kb_config(raw: dict | None) -> KBConfig:
    """Parse a raw config dict into a validated ``KBConfig``."""
    return KBConfig(**(raw or {}))
