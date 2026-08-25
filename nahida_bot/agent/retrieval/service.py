"""Small retrieval service for dispatching requests to source adapters."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from typing import Protocol

from nahida_bot.agent.retrieval.models import (
    RetrievalRequest,
    RetrievalResult,
    RetrievalSourceType,
)
from nahida_bot.agent.storage.vector import reciprocal_rank_fusion


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

    async def retrieve_fused(
        self,
        requests: Sequence[tuple[RetrievalSourceType, RetrievalRequest]],
        *,
        limit: int,
        weights: Mapping[str, float] | Sequence[float] | None = None,
    ) -> list[RetrievalResult]:
        """Retrieve from several sources at once and fuse by weighted RRF.

        Each ``(source_type, request)`` pair runs against its registered
        adapter; the per-source ranked lists are then fused with
        :func:`reciprocal_rank_fusion` (already weighted for the two-channel
        hybrid case) into one ordering. Result ids are namespaced by source
        type before fusing so an id collision across stores can never merge
        two distinct objects. The fused score replaces each result's raw
        score (also kept under ``metadata['fused_score']``) — raw scores from
        different sources are not comparable.

        ``weights`` is either a sequence aligned with ``requests`` or a
        mapping keyed by source type (unmapped sources default to 1.0);
        ``None`` keeps equal weighting. A source without a registered adapter
        or one that raises is skipped — partial fusion beats no recall — and
        sequence weights stay aligned to their original request position.
        """
        limit = max(0, int(limit))
        if limit <= 0:
            return []
        # (position, source_type, results) for sources that returned anything;
        # keeping the request position lets sequence weights survive skips.
        collected: list[tuple[int, RetrievalSourceType, list[RetrievalResult]]] = []
        for position, (source_type, request) in enumerate(requests):
            adapter = self._adapters.get(source_type)
            if adapter is None:
                continue
            try:
                results = await adapter.retrieve(request)
            except Exception:
                continue
            if results:
                collected.append((position, source_type, results))
        if not collected:
            return []

        ranked_lists: list[list[str]] = []
        by_key: dict[str, RetrievalResult] = {}
        for _, source_type, results in collected:
            ranked: list[str] = []
            for result in results:
                key = f"{source_type}:{result.result_id}"
                ranked.append(key)
                by_key[key] = result
            ranked_lists.append(ranked)

        positional_weights: list[float] | None
        if weights is None:
            positional_weights = None
        elif isinstance(weights, Mapping):
            positional_weights = [
                float(weights.get(source_type, 1.0)) for _, source_type, _ in collected
            ]
        else:
            weights_seq = [float(w) for w in weights]
            if len(weights_seq) != len(requests):
                raise ValueError(
                    f"weights length ({len(weights_seq)}) must match "
                    f"requests length ({len(requests)})"
                )
            positional_weights = [weights_seq[position] for position, _, _ in collected]

        fused = reciprocal_rank_fusion(
            ranked_lists,
            limit=limit,
            weights=positional_weights,
        )
        output: list[RetrievalResult] = []
        for key, score in fused:
            result = by_key.get(key)
            if result is None:
                continue
            output.append(
                dataclasses.replace(
                    result,
                    score=float(score),
                    metadata={**result.metadata, "fused_score": float(score)},
                )
            )
        return output
