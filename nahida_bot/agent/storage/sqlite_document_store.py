"""SQLite-backed implementation of the generic DocumentStore."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from nahida_bot.agent.storage.document_store import BackfillResult, DocumentStore
from nahida_bot.agent.storage.embedding import EmbeddingProvider, memory_text_hash
from nahida_bot.agent.storage.models import (
    DocumentEmbedding,
    DocumentItem,
    SearchResult,
)
from nahida_bot.agent.storage.repository import SQLiteDocumentRepository
from nahida_bot.agent.storage.tokenization import build_fts_query, tokenize_for_fts
from nahida_bot.agent.storage.vector import (
    VectorIndex,
    VectorRecord,
    cosine_similarity,
    reciprocal_rank_fusion,
)
from nahida_bot.db.engine import DatabaseEngine

logger = structlog.get_logger(__name__)


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
    """Build the text to embed for a document (title + content)."""
    parts = [item.title, item.content] if item.title else [item.content]
    return "\n".join(parts)


def _embedding_id_for(
    *,
    doc_id: str,
    provider_id: str,
    model: str,
    content_hash: str,
) -> str:
    """Build a stable embedding id for repeatable document upserts."""
    key = "\0".join([doc_id, provider_id, model, content_hash])
    return f"docemb_{memory_text_hash(key)[:32]}"


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
    ) -> None:
        title_index = tokenize_for_fts(title) if title else ""
        content_index = tokenize_for_fts(content)
        await self._repo.insert_document(
            doc_id=doc_id,
            title=title,
            content=content,
            metadata=metadata,
            title_index=title_index,
            content_index=content_index,
        )

    async def get(self, doc_id: str) -> DocumentItem | None:
        row = await self._repo.get_document(doc_id)
        if row is None:
            return None
        return _row_to_item(row)

    async def delete(self, doc_id: str) -> bool:
        return await self._repo.delete_document(doc_id)

    async def count(self) -> int:
        return await self._repo.count_documents()

    # ── FTS Search ────────────────────────────────────────

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        fts_query = build_fts_query(query)
        if not fts_query:
            # No query — list recent documents instead.
            rows = await self._repo.list_documents(limit=limit)
        else:
            rows = await self._repo.search_documents_fts(fts_query, limit=limit)
        return [
            SearchResult(
                doc_id=str(row["doc_id"]),
                title=str(row.get("title", "")),
                content=str(row.get("content", "")),
                score=float(row.get("score", 0.0)),
                metadata=row.get("metadata", {}),
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
            return results[:limit]

        # Fallback: cosine similarity over stored embeddings.
        rows = await self._repo.list_embeddings(
            provider_id=embedded[0].provider_id,
            model=embedded[0].model,
            dimensions=len(query_embedding),
        )
        embeddings = [_row_to_embedding(row) for row in rows]
        ranked = sorted(
            (
                (
                    emb.doc_id,
                    cosine_similarity(query_embedding, emb.embedding),
                )
                for emb in embeddings
            ),
            key=lambda item: item[1],
            reverse=True,
        )[:limit]
        doc_rows = await self._repo.get_documents_by_ids(
            [doc_id for doc_id, _score in ranked]
        )
        score_by_id = {doc_id: score for doc_id, score in ranked}
        return [
            _row_to_search_result(row, score=score_by_id.get(str(row["doc_id"]), 0.0))
            for row in doc_rows
        ]

    # ── Hybrid Search ─────────────────────────────────────

    async def search_hybrid(
        self,
        query: str,
        provider: EmbeddingProvider,
        *,
        limit: int = 10,
        vector_index: VectorIndex | None = None,
    ) -> list[SearchResult]:
        fts_results = await self.search(query, limit=limit)
        vector_results = await self.search_vector(
            query, provider, limit=limit, vector_index=vector_index
        )

        if not vector_results:
            return fts_results
        if not fts_results:
            return vector_results

        fused = reciprocal_rank_fusion(
            [
                [r.doc_id for r in fts_results],
                [r.doc_id for r in vector_results],
            ],
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
        embedding_id = _embedding_id_for(
            doc_id=doc_id,
            provider_id=provider_id,
            model=model,
            content_hash=content_hash,
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
        if not pending:
            return BackfillResult(added=0, needed=0)
        results = await provider.embed_texts([text for _, text in pending])
        added = 0
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
        return BackfillResult(added=added, needed=len(pending))


def _row_to_search_result(row: dict[str, Any], *, score: float = 0.0) -> SearchResult:
    """Convert a repository row dict into a SearchResult."""
    return SearchResult(
        doc_id=str(row.get("doc_id", "")),
        title=str(row.get("title", "")),
        content=str(row.get("content", "")),
        score=score,
        metadata=row.get("metadata", {}),
    )
