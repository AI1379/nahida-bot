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
    """Adapter from ``DocumentStore`` search methods to common retrieval results."""

    def __init__(
        self,
        *,
        collection_name: str,
        store: Any,
        ensure_vector_ready: VectorReadyCallback | None = None,
        logger: Any | None = None,
        vector_failure_event: str = "retrieval.kb_vector_search_failed",
    ) -> None:
        self._collection_name = collection_name
        self._store = store
        self._ensure_vector_ready = ensure_vector_ready
        self._logger = logger
        self._vector_failure_event = vector_failure_event

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
                    return [
                        _document_result_to_retrieval(
                            result,
                            collection_name=self._collection_name,
                            mode=mode,
                        )
                        for result in results
                        if result.score >= request.min_score
                    ]
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
        return [
            _document_result_to_retrieval(
                result,
                collection_name=self._collection_name,
                mode="fts",
            )
            for result in results
            if result.score >= request.min_score
        ]

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

    ``min_score <= 0`` disables filtering so the default request preserves the
    raw top-k ordering. Filtering happens per scope in the memory cascade so a
    weak chat-scoped hit does not consume budget that the global fallback could
    fill with a stronger match.
    """
    if min_score <= 0.0:
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
            metadata=metadata,
        ),
        metadata={
            **metadata,
            "collection": collection_name,
            "doc_id": doc_id,
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
