"""SQLite-backed implementation of the generic DocumentStore."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from nahida_bot.agent.storage.document_store import BackfillResult, DocumentStore
from nahida_bot.agent.storage.embedding import (
    EmbeddingProvider,
    memory_text_hash,
    stable_embedding_id,
)
from nahida_bot.agent.storage.models import (
    DocumentEmbedding,
    DocumentItem,
    SearchResult,
)
from nahida_bot.agent.storage.repository import SQLiteDocumentRepository
from nahida_bot.agent.storage.tokenization import (
    alias_terms,
    build_fts_and_query,
    build_fts_query,
    tokenize_for_fts,
)
from nahida_bot.agent.storage.vector import (
    VectorIndex,
    VectorRecord,
    fuse_hybrid_rankings,
    hybrid_candidate_limit,
    rank_by_cosine,
)
from nahida_bot.db.engine import DatabaseEngine

logger = structlog.get_logger(__name__)

# Batch size for the index-rebuild path that replays persisted embeddings.
_INDEX_REBUILD_BATCH = 500


def _passages_only(results: list[SearchResult]) -> list[SearchResult]:
    """Drop structural (title-only) nodes from a result list, keeping order.

    ``document`` / ``section`` nodes carry no answer content — only the
    heading or file name — so they waste top-k slots wherever they surface
    (FTS excludes them in SQL; vector hydration filters here).
    """
    return [r for r in results if r.node_type == "passage"]


def _row_to_item(row: dict[str, Any]) -> DocumentItem:
    """Convert a repository row dict into a DocumentItem."""
    created_at_raw = row.get("created_at", "")
    updated_at_raw = row.get("updated_at", "")
    return DocumentItem(
        doc_id=str(row.get("doc_id", "")),
        title=str(row.get("title", "")),
        content=str(row.get("content", "")),
        status=str(row.get("status", "active")),
        metadata=row.get("metadata", {}),
        created_at=_parse_dt(created_at_raw),
        updated_at=_parse_dt(updated_at_raw),
        retrieval_text=str(row.get("retrieval_text", "")),
        path=str(row.get("path", "")),
        source_id=str(row.get("source_id", "")),
        chunk_index=int(row.get("chunk_index", 0) or 0),
        parent_id=str(row.get("parent_id", "")),
        root_id=str(row.get("root_id", "")),
        node_type=str(row.get("node_type") or "passage"),
    )


def _row_to_embedding(row: dict[str, Any]) -> DocumentEmbedding:
    """Convert a repository row dict into a DocumentEmbedding."""
    created_at_raw = row.get("created_at", "")
    return DocumentEmbedding(
        embedding_id=str(row.get("embedding_id", "")),
        doc_id=str(row.get("doc_id", "")),
        provider_id=str(row.get("provider_id", "")),
        model=str(row.get("model", "")),
        dimensions=int(row.get("dimensions", 0)),
        content_hash=str(row.get("content_hash", "")),
        embedding=row.get("embedding", []),
        created_at=_parse_dt(created_at_raw),
    )


def _doc_embedding_text(item: DocumentItem) -> str:
    """Build the text to embed for a document.

    Uses the enriched ``retrieval_text`` (source title + heading path + content)
    when present; falls back to title + content for pre-Phase-1 rows that have
    no retrieval_text yet.
    """
    if item.retrieval_text:
        return item.retrieval_text
    parts = [item.title, item.content] if item.title else [item.content]
    return "\n".join(parts)


def _parse_dt(raw: str) -> datetime:
    """Parse an ISO datetime string, falling back to now."""
    if not raw:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return datetime.now(UTC)


class SQLiteDocumentStore(DocumentStore):
    """SQLite-backed document store for a single collection.

    Each instance manages one collection with its own set of tables:
    ``{collection}_docs``, ``{collection}_doc_fts``, and
    ``{collection}_doc_embeddings``.
    """

    def __init__(
        self,
        engine: DatabaseEngine,
        *,
        collection: str,
    ) -> None:
        self._repo = SQLiteDocumentRepository(engine, collection=collection)

    @property
    def collection(self) -> str:
        return self._repo.collection

    async def setup(self) -> None:
        """Create tables and indexes for this collection."""
        await self._repo.create_tables()
        logger.debug(
            "document_store.setup",
            collection=self.collection,
        )

    # ── Document CRUD ─────────────────────────────────────

    async def put(
        self,
        doc_id: str,
        content: str,
        *,
        title: str = "",
        metadata: dict[str, Any] | None = None,
        retrieval_text: str = "",
        path: str = "",
        source_id: str = "",
        chunk_index: int = 0,
        parent_id: str = "",
        root_id: str = "",
        node_type: str = "passage",
    ) -> None:
        # FTS indexes the enriched retrieval text (source + heading path +
        # content) when present; falls back to title + content for pre-Phase-1
        # callers so old behavior is preserved.
        index_text = retrieval_text or (f"{title}\n{content}" if title else content)
        title_index = tokenize_for_fts(title) if title else ""
        content_index = tokenize_for_fts(index_text)
        await self._repo.insert_document(
            doc_id=doc_id,
            title=title,
            content=content,
            metadata=metadata,
            title_index=title_index,
            content_index=content_index,
            retrieval_text=retrieval_text,
            path=path,
            source_id=source_id,
            chunk_index=chunk_index,
            parent_id=parent_id,
            root_id=root_id,
            node_type=node_type,
        )

    async def get_neighbors(
        self,
        source_id: str,
        *,
        chunk_index: int,
        before: int = 1,
        after: int = 1,
    ) -> list[SearchResult]:
        """Return sibling chunks adjacent to one chunk in the same source."""
        rows = await self._repo.get_neighbors(
            source_id,
            chunk_index=chunk_index,
            before=before,
            after=after,
        )
        return [_row_to_search_result(row) for row in rows]

    async def get_children(
        self, parent_id: str, *, limit: int = 50
    ) -> list[SearchResult]:
        """Return direct children of a parent node."""
        rows = await self._repo.get_children(parent_id, limit=limit)
        return [_row_to_search_result(row) for row in rows]

    async def get_subtree(
        self, root_id: str, *, limit: int = 100
    ) -> list[SearchResult]:
        """Return all nodes under a root."""
        rows = await self._repo.get_subtree(root_id, limit=limit)
        return [_row_to_search_result(row) for row in rows]

    async def get_descendants(
        self, node_id: str, *, limit: int = 100
    ) -> list[SearchResult]:
        """Return a node and all its descendants."""
        rows = await self._repo.get_descendants(node_id, limit=limit)
        return [_row_to_search_result(row) for row in rows]

    async def get_parents(self, node_id: str) -> list[SearchResult]:
        """Walk up the parent chain to the root."""
        rows = await self._repo.get_parents(node_id)
        return [_row_to_search_result(row) for row in rows]

    async def get(self, doc_id: str) -> DocumentItem | None:
        row = await self._repo.get_document(doc_id)
        if row is None:
            return None
        return _row_to_item(row)

    async def delete(self, doc_id: str) -> bool:
        return await self._repo.delete_document(doc_id)

    async def count(self) -> int:
        return await self._repo.count_documents()

    async def list_documents(
        self, *, limit: int = 50, offset: int = 0
    ) -> list[SearchResult]:
        """List active documents with pagination."""
        rows = await self._repo.list_documents(limit=limit, offset=offset)
        return [_row_to_search_result(row) for row in rows]

    # ── FTS Search ────────────────────────────────────────

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        """FTS search with alias expansion, AND tier, and OR fallback.

        Alias expansion adds same-entity surface forms detected in the query
        (草神 → 纳西妲) to the OR form only — never to the AND form, which
        would then require documents to contain every alias. With more than
        one term, the conjunction of the *original* terms is tried first: any
        document containing every term strictly dominates partial matches,
        eliminating single-term keyword collisions for exact queries
        (「七七 角色故事3」). CJK content frequently has zero documents
        containing all terms of a broad query (issue #49 probes), so the OR
        form takes over when AND matches nothing — recall is never sacrificed
        for precision.
        """
        fts_query = build_fts_query(query)
        alias_extras = alias_terms(query)
        if alias_extras:
            fts_query = " OR ".join([fts_query, *alias_extras])
        if not fts_query:
            # No query — list recent documents instead.
            rows = await self._repo.list_documents(limit=limit)
        else:
            rows: list[dict[str, Any]] = []
            and_query = build_fts_and_query(query)
            if " AND " in and_query:
                rows = await self._repo.search_documents_fts(and_query, limit=limit)
            if not rows:
                rows = await self._repo.search_documents_fts(fts_query, limit=limit)
        return [
            SearchResult(
                doc_id=str(row["doc_id"]),
                title=str(row.get("title", "")),
                content=str(row.get("content", "")),
                score=float(row.get("score", 0.0)),
                metadata=row.get("metadata", {}),
                path=str(row.get("path", "")),
                source_id=str(row.get("source_id", "")),
                chunk_index=int(row.get("chunk_index", 0) or 0),
                parent_id=str(row.get("parent_id", "")),
                root_id=str(row.get("root_id", "")),
                node_type=str(row.get("node_type") or "passage"),
            )
            for row in rows
        ]

    # ── Vector Search ─────────────────────────────────────

    async def search_vector(
        self,
        query: str,
        provider: EmbeddingProvider,
        *,
        limit: int = 10,
        vector_index: VectorIndex | None = None,
    ) -> list[SearchResult]:
        embedded = await provider.embed_texts([query])
        if not embedded or not embedded[0].embedding:
            return []
        query_embedding = embedded[0].embedding

        if vector_index is not None:
            # Over-fetch 3x: structural (title-only) nodes are filtered after
            # hydration, and the index has no node_type column to filter
            # earlier, so headroom keeps a full page of passages.
            hits = await vector_index.search(
                query_embedding, limit=max(limit * 3, limit)
            )
            if not hits:
                return []
            score_by_id = {hit.item_id: hit.score for hit in hits}
            rows = await self._repo.get_documents_by_ids([hit.item_id for hit in hits])
            results = [
                _row_to_search_result(
                    row, score=score_by_id.get(str(row["doc_id"]), 0.0)
                )
                for row in rows
            ]
            return _passages_only(results)[:limit]

        # Fallback: cosine similarity over stored embeddings.
        rows = await self._repo.list_embeddings(
            provider_id=embedded[0].provider_id,
            model=embedded[0].model,
            dimensions=len(query_embedding),
        )
        embeddings = [_row_to_embedding(row) for row in rows]
        ranked = rank_by_cosine(
            query_embedding,
            ((embedding.doc_id, embedding.embedding) for embedding in embeddings),
            limit=max(limit * 2, limit),
        )
        doc_rows = await self._repo.get_documents_by_ids(
            [doc_id for doc_id, _score in ranked]
        )
        score_by_id = {doc_id: score for doc_id, score in ranked}
        return _passages_only(
            [
                _row_to_search_result(
                    row, score=score_by_id.get(str(row["doc_id"]), 0.0)
                )
                for row in doc_rows
            ]
        )[:limit]

    # ── Hybrid Search ─────────────────────────────────────

    async def search_hybrid(
        self,
        query: str,
        provider: EmbeddingProvider,
        *,
        limit: int = 10,
        vector_index: VectorIndex | None = None,
    ) -> list[SearchResult]:
        """Fuse FTS and vector rankings over an amplified candidate pool.

        Two properties matter here (issue #49, verified against production
        data): each leg must fetch far more candidates than the final ``limit``
        — otherwise the correct chunk never enters fusion because it sits at
        FTS rank 8+ while only the final top-k is fetched — and the FTS leg
        must carry a lower RRF weight, because BM25 keyword collisions are
        cheap while an embedding rank-1 hit is the semantic answer. Under
        equal weights the two tie at exactly ``1/(k+1)`` and stable sorting
        lets FTS junk win every tie.
        """
        candidate_limit = hybrid_candidate_limit(limit)
        fts_results = await self.search(query, limit=candidate_limit)
        vector_results = await self.search_vector(
            query, provider, limit=candidate_limit, vector_index=vector_index
        )

        if not vector_results:
            # Degrading to FTS is correct, but it must be visible: a silently
            # empty vector channel (wiped index, provider outage) is exactly
            # how #49's "semantic layer does nothing" state went unnoticed.
            logger.warning(
                "document_store.hybrid_vector_empty",
                collection=self.collection,
                fallback="fts",
            )
            return fts_results[:limit]
        if not fts_results:
            return vector_results[:limit]

        fused = fuse_hybrid_rankings(
            [result.doc_id for result in fts_results],
            [result.doc_id for result in vector_results],
            limit=limit,
        )
        rows = await self._repo.get_documents_by_ids(
            [doc_id for doc_id, _score in fused]
        )
        score_by_id = dict(fused)
        return [
            _row_to_search_result(row, score=score_by_id.get(str(row["doc_id"]), 0.0))
            for row in rows
        ]

    # ── Embedding Management ──────────────────────────────

    async def put_embedding(
        self,
        doc_id: str,
        embedding: list[float],
        *,
        provider_id: str,
        model: str,
        content_hash: str,
        vector_index: VectorIndex | None = None,
    ) -> str:
        embedding_id = stable_embedding_id(
            doc_id,
            provider_id,
            model,
            content_hash,
            prefix="docemb",
        )
        stale_ids = [
            existing_id
            for existing_id in await self._repo.list_embedding_ids_for_doc(
                doc_id,
                provider_id=provider_id,
                model=model,
            )
            if existing_id != embedding_id
        ]
        if stale_ids:
            if vector_index is not None:
                await vector_index.delete(stale_ids)
            await self._repo.delete_embeddings(stale_ids)
        await self._repo.upsert_embedding(
            embedding_id=embedding_id,
            doc_id=doc_id,
            provider_id=provider_id,
            model=model,
            dimensions=len(embedding),
            content_hash=content_hash,
            embedding=embedding,
        )
        if vector_index is not None:
            await vector_index.upsert(
                [
                    VectorRecord(
                        embedding_id=embedding_id,
                        item_id=doc_id,
                        embedding=embedding,
                    )
                ]
            )
        return embedding_id

    async def embed_documents(
        self,
        provider: EmbeddingProvider,
        *,
        limit: int = 100,
        vector_index: VectorIndex | None = None,
    ) -> BackfillResult:
        """Embed active documents lacking a current embedding for this provider+model.

        Documents whose current embedding text already has a persisted vector for
        the given provider and model are skipped (``content_hash`` dedup), so the
        expensive embedding call only runs for new or changed content. This makes
        the backfill idempotent across calls and process restarts.

        When nothing needs (re-)embedding but a vector index is attached and out
        of sync with the persisted embeddings, the index is rebuilt from the
        stored vectors — no embedding API calls. Without this, a wiped or
        recreated-empty index would never refill (every document is "already
        embedded"), which is exactly how #49's silent FTS-only degradation
        happened after a manual index cleanup.
        """
        rows = await self._repo.list_documents(limit=limit)
        items = [_row_to_item(row) for row in rows]
        existing = await self._repo.list_embedding_keys(
            provider_id=provider.provider_id,
            model=provider.model,
        )
        pending: list[tuple[DocumentItem, str]] = []
        for item in items:
            text = _doc_embedding_text(item)
            if (item.doc_id, memory_text_hash(text)) in existing:
                continue
            pending.append((item, text))
        added = 0
        if pending:
            results = await provider.embed_texts([text for _, text in pending])
            for (item, text), result in zip(pending, results, strict=False):
                if not result.embedding:
                    continue
                await self.put_embedding(
                    item.doc_id,
                    result.embedding,
                    provider_id=result.provider_id,
                    model=result.model,
                    content_hash=memory_text_hash(text),
                    vector_index=vector_index,
                )
                added += 1
            needed = len(pending)
        else:
            needed = 0
            if (
                vector_index is not None
                and existing
                and callable(getattr(vector_index, "count", None))
                and callable(getattr(vector_index, "embedding_ids", None))
            ):
                await self._rebuild_vector_index_if_stale(
                    provider, vector_index, expected_keys=existing
                )
        return BackfillResult(added=added, needed=needed)

    async def _rebuild_vector_index_if_stale(
        self,
        provider: EmbeddingProvider,
        vector_index: VectorIndex,
        *,
        expected_keys: set[tuple[str, str]],
    ) -> None:
        """Replay persisted embeddings into the index when counts diverge.

        The ``{collection}_doc_embeddings`` table is authoritative; the index is
        derived and disposable. Rebuild streams rows in batches so a 14k×4096
        collection fits in bounded memory, and clears index rows whose
        embedding ids no longer exist in the table (documents deleted or
        re-embedded since).
        """
        expected_ids = {
            embedding_id
            for embedding_id in await self._repo.list_all_embedding_ids(
                provider_id=provider.provider_id, model=provider.model
            )
        }
        if not expected_ids:
            return
        index_count = await vector_index.count()
        if index_count == len(expected_ids):
            return
        logger.warning(
            "document_store.vector_index_stale",
            collection=self.collection,
            index_rows=index_count,
            embedding_rows=len(expected_ids),
            action="rebuild_from_embeddings",
        )
        dimensions = provider.dimensions
        if dimensions <= 0:
            logger.warning(
                "document_store.vector_index_rebuild_skipped",
                collection=self.collection,
                reason="unknown_embedding_dimensions",
            )
            return
        indexed_ids = await vector_index.embedding_ids()
        stale_ids = sorted(indexed_ids - expected_ids)
        if stale_ids:
            await vector_index.delete(stale_ids)
        offset = 0
        while True:
            batch = await self._repo.list_embeddings(
                provider_id=provider.provider_id,
                model=provider.model,
                dimensions=dimensions,
                limit=_INDEX_REBUILD_BATCH,
                offset=offset,
            )
            if not batch:
                break
            records = [
                VectorRecord(
                    embedding_id=row["embedding_id"],
                    item_id=row["doc_id"],
                    embedding=row["embedding"],
                )
                for row in batch
            ]
            await vector_index.upsert(records)
            offset += len(batch)
            if len(batch) < _INDEX_REBUILD_BATCH:
                break


def _row_to_search_result(row: dict[str, Any], *, score: float = 0.0) -> SearchResult:
    """Convert a repository row dict into a SearchResult."""
    return SearchResult(
        doc_id=str(row.get("doc_id", "")),
        title=str(row.get("title", "")),
        content=str(row.get("content", "")),
        score=score,
        metadata=row.get("metadata", {}),
        path=str(row.get("path", "")),
        source_id=str(row.get("source_id", "")),
        chunk_index=int(row.get("chunk_index", 0) or 0),
        parent_id=str(row.get("parent_id", "")),
        root_id=str(row.get("root_id", "")),
        node_type=str(row.get("node_type") or "passage"),
    )
