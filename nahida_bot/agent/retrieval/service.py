"""Small retrieval service for dispatching requests to source adapters."""

from __future__ import annotations

from typing import Protocol

from nahida_bot.agent.retrieval.models import (
    RetrievalRequest,
    RetrievalResult,
    RetrievalSourceType,
)


class RetrievalAdapter(Protocol):
    """Adapter interface for one retrieval source."""

    async def retrieve(self, request: RetrievalRequest) -> list[RetrievalResult]:
        """Return ranked retrieval results for one request."""
        ...


class RetrievalService:
    """Dispatch common retrieval requests to registered source adapters."""

    def __init__(
        self,
        adapters: dict[RetrievalSourceType, RetrievalAdapter] | None = None,
    ) -> None:
        self._adapters: dict[RetrievalSourceType, RetrievalAdapter] = dict(
            adapters or {}
        )

    def register(
        self,
        source_type: RetrievalSourceType,
        adapter: RetrievalAdapter,
    ) -> None:
        """Register or replace an adapter for a source type."""
        self._adapters[source_type] = adapter

    async def retrieve(self, request: RetrievalRequest) -> list[RetrievalResult]:
        """Retrieve results from the adapter named by ``request.source_type``."""
        adapter = self._adapters.get(request.source_type)
        if adapter is None:
            return []
        return await adapter.retrieve(request)
