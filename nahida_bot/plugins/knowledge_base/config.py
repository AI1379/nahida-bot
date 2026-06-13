"""Knowledge Base plugin configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field


class KBConfig(BaseModel):
    """Configuration for the Knowledge Base plugin.

    Parsed from the ``config`` block in ``plugin.yaml`` and merged
    with any top-level ``knowledge_base:`` key in ``config.yaml``.
    """

    enabled: bool = True
    default_chunk_size: int = Field(
        default=500,
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


def parse_kb_config(raw: dict | None) -> KBConfig:
    """Parse a raw config dict into a validated ``KBConfig``."""
    if not raw:
        return KBConfig()
    return KBConfig(**{k: v for k, v in raw.items() if k in KBConfig.model_fields})
