"""Retrieval adapters over the existing Memory and KB storage APIs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

from nahida_bot.agent.memory.scope import (
    SCOPE_ID_GLOBAL,
    SCOPE_TYPE_CHAT,
    SCOPE_TYPE_GLOBAL,
)
from nahida_bot.agent.retrieval.models import (
    RetrievalMode,
    RetrievalProvenance,
    RetrievalRequest,
    RetrievalResult,
    RetrievalScope,
)
from nahida_bot.agent.storage.tokenization import build_fts_query

VectorReadyCallback = Callable[[], Awaitable[tuple[Any | None, Any | None]]]


class DocumentStoreRetrievalAdapter:
    """Adapter from ``DocumentStore`` search methods to common retrieval results.

    Supports optional **neighbor expansion**: when enabled, the top
    ``expand_neighbors_top_k`` results each pull ±1 sibling chunks (same
    ``source_id``, adjacent ``chunk_index``) and append them with a lower score
    and ``neighbor_of`` provenance in metadata.
    """

    def __init__(
        self,
        *,
        collection_name: str,
        store: Any,
        ensure_vector_ready: VectorReadyCallback | None = None,
        logger: Any | None = None,
        vector_failure_event: str = "retrieval.kb_vector_search_failed",
        expand_neighbors: bool = False,
        expand_neighbors_top_k: int = 3,
    ) -> None:
        self._collection_name = collection_name
        self._store = store
        self._ensure_vector_ready = ensure_vector_ready
        self._logger = logger
        self._vector_failure_event = vector_failure_event
        self._expand_neighbors = expand_neighbors
        self._expand_neighbors_top_k = expand_neighbors_top_k

    async def retrieve(self, request: RetrievalRequest) -> list[RetrievalResult]:
        """Search the document store using FTS, vector, or hybrid mode."""
        query = request.query.strip()
        limit = max(0, int(request.limit))
        if not query or limit <= 0:
            return []

        fts_enabled = request.fts_enabled and bool(build_fts_query(query))
        vector_enabled = request.vector_enabled
        if not fts_enabled and not vector_enabled:
            return []

        mode = _select_mode(
            fts_enabled=fts_enabled,
            vector_enabled=vector_enabled,
            hybrid_enabled=request.hybrid_enabled,
        )
        results: list[Any]
        if mode in {"hybrid", "vector"}:
            provider, vector_index = await self._ready_vector_search()
            if provider is not None:
                try:
                    if mode == "hybrid":
                        results = await self._store.search_hybrid(
                            query,
                            provider,
                            limit=limit,
                            vector_index=vector_index,
                        )
                    else:
                        results = await self._store.search_vector(
                            query,
                            provider,
                            limit=limit,
                            vector_index=vector_index,
                        )
                    converted = _above_threshold(
                        [
                            _document_result_to_retrieval(
                                result,
                                collection_name=self._collection_name,
                                mode=mode,
                            )
                            for result in results
                        ],
                        request.min_score,
                    )
                    return await self._maybe_expand_neighbors(converted)
                except Exception as exc:
                    if self._logger is not None:
                        self._logger.warning(
                            self._vector_failure_event,
                            collection=self._collection_name,
                            mode=mode,
                            error=str(exc),
                            fallback="fts" if fts_enabled else "none",
                        )
            if not fts_enabled:
                return []

        results = await self._store.search(query, limit=limit)
        converted = _above_threshold(
            [
                _document_result_to_retrieval(
                    result,
                    collection_name=self._collection_name,
                    mode="fts",
                )
                for result in results
            ],
            request.min_score,
        )
        return await self._maybe_expand_neighbors(converted)

    async def _maybe_expand_neighbors(
        self, results: list[RetrievalResult]
    ) -> list[RetrievalResult]:
        """Optionally expand the top results with adjacent sibling chunks."""
        if not self._expand_neighbors or not results:
            return results
        get_neighbors = getattr(self._store, "get_neighbors", None)
        if not callable(get_neighbors):
            return results
        get_neighbors = cast(Any, get_neighbors)

        expanded: list[RetrievalResult] = []
        seen: set[str] = {r.result_id for r in results}
        top_k = max(0, self._expand_neighbors_top_k)
        for rank, result in enumerate(results):
            expanded.append(result)
            if rank >= top_k:
                continue
            source_id = _source_id_from_result(result)
            chunk_index = _chunk_index_from_result(result)
            if not source_id:
                continue
            try:
                neighbors = await get_neighbors(
                    source_id,
                    chunk_index=chunk_index,
                    before=1,
                    after=1,
                )
            except Exception:
                continue
            for neighbor_raw in neighbors or []:
                n_doc_id = str(getattr(neighbor_raw, "doc_id", ""))
                if n_doc_id in seen or n_doc_id == result.result_id:
                    continue
                seen.add(n_doc_id)
                n_result = _document_result_to_retrieval(
                    neighbor_raw,
                    collection_name=self._collection_name,
                    mode=result.mode,
                )
                expanded.append(
                    RetrievalResult(
                        result_id=n_result.result_id,
                        title=n_result.title,
                        text=n_result.text,
                        source_type=n_result.source_type,
                        score=n_result.score * 0.8,
                        mode=n_result.mode,
                        provenance=n_result.provenance,
                        metadata={
                            **n_result.metadata,
                            "neighbor_of": result.result_id,
                        },
                        raw=n_result.raw,
                    )
                )
        return expanded

    async def _ready_vector_search(self) -> tuple[Any | None, Any | None]:
        if self._ensure_vector_ready is None:
            return None, None
        return await self._ensure_vector_ready()


class MemoryStoreRetrievalAdapter:
    """Adapter from durable memory item search to common retrieval results."""

    def __init__(
        self,
        *,
        memory_store: Any,
        embedding_provider: Any | None = None,
        vector_index: Any | None = None,
    ) -> None:
        self._memory_store = memory_store
        self._embedding_provider = embedding_provider
        self._vector_index = vector_index

    async def retrieve(self, request: RetrievalRequest) -> list[RetrievalResult]:
        """Search memory, cascading across the request's scopes in priority order.

        ``request.scopes`` (an ordered tuple) drives an N-level cascade: each
        scope is searched with the remaining result budget and deduped against
        earlier scopes by ``result_id``. When ``scopes`` is empty, the legacy
        ``scope`` / ``allow_global_fallback`` pair is used (chat -> global),
        preserving V1 behavior exactly. The identity-aware read cascade
        (person -> account -> chat -> global) is built by the caller and passed
        here as ``scopes``; see ``nahida_bot.identity.policy``.
        """
        query = request.query.strip()
        limit = max(0, int(request.limit))
        if not query or limit <= 0:
            return []

        fts_enabled = request.fts_enabled and bool(build_fts_query(query))
        vector_enabled = request.vector_enabled and self._embedding_provider is not None
        if not fts_enabled and not vector_enabled:
            return []

        mode = _select_mode(
            fts_enabled=fts_enabled,
            vector_enabled=vector_enabled,
            hybrid_enabled=request.hybrid_enabled,
        )
        return await self._cascade(
            self._effective_scopes(request), query, limit, mode, request.min_score
        )

    @staticmethod
    def _effective_scopes(request: RetrievalRequest) -> list[RetrievalScope]:
        """Resolve the ordered scope list for a request.

        An explicit ``scopes`` tuple wins. Otherwise the legacy single ``scope``
        is used, expanded to ``[scope, global]`` when ``allow_global_fallback``
        is set on a chat scope. A missing scope defaults to global.
        """
        if request.scopes:
            return list(request.scopes)
        scope = request.scope or RetrievalScope(SCOPE_TYPE_GLOBAL, SCOPE_ID_GLOBAL)
        if scope.scope_type == SCOPE_TYPE_CHAT and request.allow_global_fallback:
            return [scope, RetrievalScope(SCOPE_TYPE_GLOBAL, SCOPE_ID_GLOBAL)]
        return [scope]

    async def _cascade(
        self,
        scopes: list[RetrievalScope],
        query: str,
        limit: int,
        mode: RetrievalMode,
        min_score: float,
    ) -> list[RetrievalResult]:
        """Search each scope in order, filling the budget, deduped by id.

        Each scope is searched with only the *remaining* budget so an earlier
        scope can't starve later ones by over-fetching. Per-scope
        ``min_score`` filtering means a below-threshold hit does not steal a
        slot that a stronger match in a later scope could fill.
        """
        results: list[RetrievalResult] = []
        seen: set[str] = set()
        remaining = limit
        for scope in scopes:
            if remaining <= 0:
                break
            scoped = _above_threshold(
                await self._search_scope(scope, query, remaining, mode=mode),
                min_score,
            )
            for item in scoped:
                if item.result_id in seen:
                    continue
                seen.add(item.result_id)
                results.append(item)
                remaining -= 1
                if remaining <= 0:
                    break
        return results

    async def _search_scope(
        self,
        scope: RetrievalScope,
        query: str,
        limit: int,
        *,
        mode: RetrievalMode,
    ) -> list[RetrievalResult]:
        if mode == "hybrid":
            search_items = getattr(self._memory_store, "search_items_hybrid", None)
            if not callable(search_items):
                return await self._search_scope(scope, query, limit, mode="fts")
            search_items = cast(Any, search_items)
            items = await search_items(
                query,
                self._embedding_provider,
                scope_type=scope.scope_type,
                scope_id=scope.scope_id,
                limit=limit,
                vector_index=self._vector_index,
            )
        elif mode == "vector":
            search_items = getattr(self._memory_store, "search_items_vector", None)
            if not callable(search_items):
                return []
            search_items = cast(Any, search_items)
            items = await search_items(
                query,
                self._embedding_provider,
                scope_type=scope.scope_type,
                scope_id=scope.scope_id,
                limit=limit,
                vector_index=self._vector_index,
            )
        else:
            search_items = getattr(self._memory_store, "search_items", None)
            if not callable(search_items):
                return []
            search_items = cast(Any, search_items)
            items = await search_items(
                query,
                scope_type=scope.scope_type,
                scope_id=scope.scope_id,
                limit=limit,
            )
        return [_memory_item_to_retrieval(item, mode=mode) for item in list(items)]


def _above_threshold(
    items: list[RetrievalResult],
    min_score: float,
) -> list[RetrievalResult]:
    """Drop results below ``min_score``.

    ``min_score`` is compared against each result's score; results below the
    threshold are dropped.  The sentinel value ``float('-inf')`` (the default)
    means "no filtering", preserving the raw top-k ordering.  Filtering happens
    per scope in the memory cascade so a weak chat-scoped hit does not consume
    budget that the global fallback could fill with a stronger match.
    """
    if min_score == float("-inf"):
        return items
    return [item for item in items if item.score >= min_score]


def _select_mode(
    *,
    fts_enabled: bool,
    vector_enabled: bool,
    hybrid_enabled: bool,
) -> RetrievalMode:
    if vector_enabled and fts_enabled and hybrid_enabled:
        return "hybrid"
    if vector_enabled:
        return "vector"
    if fts_enabled:
        return "fts"
    return "none"


def _document_result_to_retrieval(
    result: Any,
    *,
    collection_name: str,
    mode: RetrievalMode,
) -> RetrievalResult:
    metadata = _dict_metadata(getattr(result, "metadata", {}))
    doc_id = str(getattr(result, "doc_id", ""))
    path = str(getattr(result, "path", ""))
    source_id = str(getattr(result, "source_id", ""))
    chunk_index = int(getattr(result, "chunk_index", 0) or 0)
    return RetrievalResult(
        result_id=doc_id,
        title=str(getattr(result, "title", "")),
        text=str(getattr(result, "content", "")),
        source_type="knowledge_base",
        score=float(getattr(result, "score", 0.0)),
        mode=mode,
        provenance=RetrievalProvenance(
            source_type="knowledge_base",
            source_id=doc_id,
            collection=collection_name,
            metadata={
                **metadata,
                "path": path,
                "source_id": source_id,
                "chunk_index": str(chunk_index),
            },
        ),
        metadata={
            **metadata,
            "collection": collection_name,
            "doc_id": doc_id,
            "path": path,
            "source_id": source_id,
            "chunk_index": str(chunk_index),
        },
        raw=result,
    )


def _memory_item_to_retrieval(item: Any, *, mode: RetrievalMode) -> RetrievalResult:
    metadata = _dict_metadata(getattr(item, "metadata", {}))
    evidence = _dict_metadata(getattr(item, "evidence", {}))
    item_id = str(getattr(item, "item_id", ""))
    scope_type = str(getattr(item, "scope_type", ""))
    scope_id = str(getattr(item, "scope_id", ""))
    kind = str(getattr(item, "kind", ""))
    result_metadata = {
        **metadata,
        "item_id": item_id,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "kind": kind,
        "source": str(getattr(item, "source", "")),
        "sensitivity": str(getattr(item, "sensitivity", "")),
        "evidence": evidence,
    }
    return RetrievalResult(
        result_id=item_id,
        title=str(getattr(item, "title", "")),
        text=str(getattr(item, "content", "")),
        source_type="memory",
        score=float(getattr(item, "score", 0.0)),
        mode=mode,
        provenance=RetrievalProvenance(
            source_type="memory",
            source_id=item_id,
            scope_type=scope_type,
            scope_id=scope_id,
            kind=kind,
            metadata=metadata,
        ),
        metadata=result_metadata,
        raw=item,
    )


def _dict_metadata(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _source_id_from_result(result: RetrievalResult) -> str:
    """Extract the source document id from a retrieval result's metadata or raw."""
    source_id = str(result.metadata.get("source_id", ""))
    if source_id:
        return source_id
    raw = result.raw
    if raw is not None:
        return str(getattr(raw, "source_id", ""))
    return ""


def _chunk_index_from_result(result: RetrievalResult) -> int:
    """Extract the chunk index from a retrieval result's metadata or raw."""
    raw_val = result.metadata.get("chunk_index", "")
    if raw_val != "" and raw_val is not None:
        return int(raw_val)
    raw = result.raw
    if raw is not None:
        return int(getattr(raw, "chunk_index", 0) or 0)
    return 0
