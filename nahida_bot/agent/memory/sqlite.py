"""SQLite-backed memory store implementation."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from nahida_bot.agent.memory.models import (
    ConversationTurn,
    MemoryCandidate,
    MemoryEmbedding,
    MemoryItem,
    MemoryRecord,
    SessionSummary,
    normalize_sensitivity,
    normalize_sensitivity_source,
)
from nahida_bot.agent.memory.scope import SCOPE_ID_GLOBAL, SCOPE_TYPE_GLOBAL
from nahida_bot.agent.memory.store import MemoryStore
from nahida_bot.agent.storage.embedding import EmbeddingProvider, memory_text_hash
from nahida_bot.agent.storage.tokenization import (
    build_fts_query,
    extract_keywords,
    tokenize_for_fts,
)
from nahida_bot.agent.storage.vector import (
    VectorIndex,
    VectorRecord,
    cosine_similarity,
    reciprocal_rank_fusion,
)
from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.db.repositories.sqlite_memory_repo import SQLiteMemoryRepository


def _row_to_record(
    row: dict[str, Any], *, keywords: list[str] | None = None
) -> MemoryRecord:
    """Convert a repository row dict into a MemoryRecord."""
    created_at_raw = row.get("created_at", "")
    if isinstance(created_at_raw, str) and created_at_raw:
        created_at = datetime.fromisoformat(created_at_raw)
    else:
        created_at = datetime.now(UTC)

    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        metadata = None

    return MemoryRecord(
        turn_id=row.get("id", 0),
        session_id=row.get("session_id", ""),
        turn=ConversationTurn(
            role=row.get("role", ""),
            content=row.get("content", ""),
            source=row.get("source", ""),
            metadata=metadata,
            created_at=created_at,
        ),
        keywords=list(keywords) if keywords else [],
    )


def _row_to_item(row: dict[str, Any]) -> MemoryItem:
    """Convert a repository row dict into a MemoryItem."""
    created_at_raw = row.get("created_at", "")
    updated_at_raw = row.get("updated_at", "")
    created_at = (
        datetime.fromisoformat(created_at_raw)
        if isinstance(created_at_raw, str) and created_at_raw
        else datetime.now(UTC)
    )
    updated_at = (
        datetime.fromisoformat(updated_at_raw)
        if isinstance(updated_at_raw, str) and updated_at_raw
        else created_at
    )
    evidence = row.get("evidence")
    metadata = row.get("metadata")
    return MemoryItem(
        item_id=str(row.get("item_id", "")),
        scope_type=str(row.get("scope_type", "")),
        scope_id=str(row.get("scope_id", "")),
        kind=str(row.get("kind", "")),
        title=str(row.get("title", "")),
        content=str(row.get("content", "")),
        status=str(row.get("status", "active")),
        confidence=float(row.get("confidence", 1.0)),
        importance=float(row.get("importance", 0.5)),
        sensitivity=normalize_sensitivity(row.get("sensitivity", "public")),
        sensitivity_source=normalize_sensitivity_source(
            row.get("sensitivity_source", "default")
        ),
        source=str(row.get("source", "plugin")),
        evidence=evidence if isinstance(evidence, dict) else {},
        metadata=metadata if isinstance(metadata, dict) else {},
        created_at=created_at,
        updated_at=updated_at,
        score=float(row.get("score", 0.0)),
        parent_id=str(row.get("parent_id", "") or ""),
        root_id=str(row.get("root_id", "") or ""),
        node_type=str(row.get("node_type", "leaf") or "leaf"),
        path=str(row.get("path", "") or ""),
        source_id=str(row.get("source_id", "") or ""),
    )


def _row_to_embedding(row: dict[str, Any]) -> MemoryEmbedding:
    """Convert a repository row dict into a MemoryEmbedding."""
    created_at_raw = row.get("created_at", "")
    created_at = (
        datetime.fromisoformat(created_at_raw)
        if isinstance(created_at_raw, str) and created_at_raw
        else datetime.now(UTC)
    )
    raw_embedding = row.get("embedding")
    embedding = raw_embedding if isinstance(raw_embedding, list) else []
    return MemoryEmbedding(
        embedding_id=str(row.get("embedding_id", "")),
        item_id=str(row.get("item_id", "")),
        provider_id=str(row.get("provider_id", "")),
        model=str(row.get("model", "")),
        dimensions=int(row.get("dimensions", 0)),
        content_hash=str(row.get("content_hash", "")),
        embedding=[float(value) for value in embedding],
        created_at=created_at,
    )


def _row_to_candidate(row: dict[str, Any]) -> MemoryCandidate:
    """Convert a repository row dict into a MemoryCandidate."""
    created_at_raw = row.get("created_at", "")
    updated_at_raw = row.get("updated_at", "")
    created_at = (
        datetime.fromisoformat(created_at_raw)
        if isinstance(created_at_raw, str) and created_at_raw
        else datetime.now(UTC)
    )
    updated_at = (
        datetime.fromisoformat(updated_at_raw)
        if isinstance(updated_at_raw, str) and updated_at_raw
        else created_at
    )
    evidence = row.get("evidence")
    metadata = row.get("metadata")
    return MemoryCandidate(
        candidate_id=str(row.get("candidate_id", "")),
        scope_type=str(row.get("scope_type", "")),
        scope_id=str(row.get("scope_id", "")),
        kind=str(row.get("kind", "")),
        title=str(row.get("title", "")),
        content=str(row.get("content", "")),
        status=str(row.get("status", "pending")),
        confidence=float(row.get("confidence", 0.5)),
        evidence=evidence if isinstance(evidence, dict) else {},
        metadata=metadata if isinstance(metadata, dict) else {},
        created_at=created_at,
        updated_at=updated_at,
    )


def _item_embedding_text(item: MemoryItem) -> str:
    """Build the text payload embedded for a durable memory item."""
    parts = [item.title.strip(), item.content.strip()]
    return "\n".join(part for part in parts if part)


def _embedding_id_for(
    *,
    item_id: str,
    provider_id: str,
    model: str,
    content_hash: str,
) -> str:
    """Build a stable embedding id for repeatable vector upserts."""
    key = "\0".join([item_id, provider_id, model, content_hash])
    return f"emb_{memory_text_hash(key)[:32]}"


class SQLiteMemoryStore(MemoryStore):
    """SQLite-backed memory store using the memory repository."""

    def __init__(self, engine: DatabaseEngine) -> None:
        self._repo = SQLiteMemoryRepository(engine)

    async def ensure_session(
        self, session_id: str, workspace_id: str | None = None
    ) -> None:
        """Ensure a session exists before storing turns."""
        await self._repo.ensure_session(session_id, workspace_id)

    async def append_turn(self, session_id: str, turn: ConversationTurn) -> int:
        """Store a conversation turn with auto-extracted keywords."""
        keywords = extract_keywords(turn.content)
        return await self._repo.append_turn(
            session_id,
            role=turn.role,
            content=turn.content,
            source=turn.source,
            metadata=turn.metadata,
            keywords=keywords,
        )

    async def search(
        self, session_id: str, query: str, *, limit: int = 10
    ) -> list[MemoryRecord]:
        """Search by query keywords with multi-keyword OR aggregation.

        Falls back to time-ordered retrieval when no keyword matches.
        """
        query_keywords = extract_keywords(query)
        if query_keywords:
            rows = await self._repo.search_by_keywords(
                session_id, query_keywords, limit=limit
            )
            if rows:
                turn_ids = [row["id"] for row in rows]
                kw_map = await self._repo.get_keywords_for_turns(turn_ids)
                return [
                    _row_to_record(row, keywords=kw_map.get(row["id"], []))
                    for row in rows
                ]

        # Fallback: return recent turns when no keyword match.
        rows = await self._repo.get_recent_turns(session_id, limit=limit)
        turn_ids = [row["id"] for row in rows]
        kw_map = await self._repo.get_keywords_for_turns(turn_ids)
        return [_row_to_record(row, keywords=kw_map.get(row["id"], [])) for row in rows]

    async def get_recent(
        self, session_id: str, *, limit: int = 50
    ) -> list[MemoryRecord]:
        """Retrieve recent turns in chronological order with keywords."""
        rows = await self._repo.get_recent_turns(session_id, limit=limit)
        turn_ids = [row["id"] for row in rows]
        kw_map = await self._repo.get_keywords_for_turns(turn_ids)
        return [_row_to_record(row, keywords=kw_map.get(row["id"], [])) for row in rows]

    async def search_turns(
        self,
        query: str = "",
        *,
        chat_address: str = "",
        source: str = "",
        role: str = "",
        limit: int = 100,
    ) -> list[MemoryRecord]:
        """Search persisted turns across sessions for admin/debug views."""
        rows = await self._repo.search_turns(
            query,
            chat_address=chat_address,
            source=source,
            role=role,
            limit=limit,
        )
        return [_row_to_record(row) for row in rows]

    async def evict_before(self, cutoff: datetime) -> int:
        """Delete turns older than cutoff datetime."""
        return await self._repo.delete_turns_before(cutoff)

    async def clear_session(self, session_id: str) -> int:
        """Delete all turns and keywords for a session."""
        return await self._repo.clear_session_turns(session_id)

    async def list_sessions(self, *, limit: int = 50) -> list[SessionSummary]:
        """List sessions with turn counts."""
        rows = await self._repo.list_sessions(limit=limit)
        return [
            SessionSummary(
                session_id=r["session_id"],
                workspace_id=r.get("workspace_id"),
                created_at=r.get("created_at", ""),
                last_active_at=r.get("last_active_at", ""),
                turn_count=r.get("turn_count", 0),
                metadata=r.get("metadata", {}),
            )
            for r in rows
        ]

    async def get_session_meta(self, session_id: str) -> dict[str, Any]:
        """Get session metadata."""
        return await self._repo.get_session_metadata(session_id)

    async def update_session_meta(
        self, session_id: str, updates: dict[str, Any]
    ) -> None:
        """Merge updates into session metadata."""
        await self._repo.update_session_metadata(session_id, updates)

    async def persist_active_session(self, chat_key: str, session_id: str) -> None:
        """Persist the active session override for a chat key."""
        await self._repo.set_active_session(chat_key, session_id)

    async def load_active_sessions(self) -> dict[str, str]:
        """Load all persisted session overrides."""
        return await self._repo.load_all_active_sessions()

    async def append_item(
        self,
        *,
        content: str,
        title: str = "",
        scope_type: str = SCOPE_TYPE_GLOBAL,
        scope_id: str = SCOPE_ID_GLOBAL,
        kind: str = "fact",
        source: str = "plugin",
        confidence: float = 1.0,
        importance: float = 0.5,
        sensitivity: str = "public",
        sensitivity_source: str = "default",
        evidence: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        item_id: str | None = None,
        parent_id: str = "",
        root_id: str = "",
        node_type: str = "leaf",
        path: str = "",
        source_id: str = "",
    ) -> str:
        """Store a durable structured memory item and update the FTS index."""
        memory_id = item_id or f"mem_{uuid4().hex}"
        # Normalize at the write boundary so the DB only ever holds canonical
        # lowercase values — the soft-scope SQL filter matches
        # ``sensitivity='public'`` exactly, so a stray casing/typo here would
        # either leak or silently under-recall.
        await self._repo.append_memory_item(
            item_id=memory_id,
            scope_type=scope_type,
            scope_id=scope_id,
            kind=kind,
            title=title,
            content=content,
            status="active",
            confidence=confidence,
            importance=importance,
            sensitivity=normalize_sensitivity(sensitivity),
            sensitivity_source=normalize_sensitivity_source(sensitivity_source),
            source=source,
            evidence=evidence,
            metadata=metadata,
            title_index=tokenize_for_fts(title),
            content_index=tokenize_for_fts(content),
            parent_id=parent_id,
            root_id=root_id,
            node_type=node_type,
            path=path,
            source_id=source_id,
        )
        return memory_id

    async def search_items(
        self,
        query: str = "",
        *,
        scope_type: str | None = SCOPE_TYPE_GLOBAL,
        scope_id: str | None = SCOPE_ID_GLOBAL,
        limit: int = 10,
    ) -> list[MemoryItem]:
        """Search durable items, optionally filtering scope type and/or id."""
        fts_query = build_fts_query(query)
        if fts_query:
            rows = await self._repo.search_memory_items(
                fts_query,
                scope_type=scope_type,
                scope_id=scope_id,
                limit=limit,
            )
        else:
            rows = await self._repo.list_memory_items(
                scope_type=scope_type,
                scope_id=scope_id,
                limit=limit,
            )
        return [_row_to_item(row) for row in rows]

    async def archive_item(self, item_id: str) -> bool:
        """Archive a durable memory item."""
        return await self._repo.archive_memory_item(item_id)

    async def get_items_by_ids(self, item_ids: list[str]) -> list[MemoryItem]:
        """Return active durable items by id in input order."""
        rows = await self._repo.get_memory_items_by_ids(item_ids)
        return [_row_to_item(row) for row in rows]

    async def search_items_public(
        self,
        query: str = "",
        *,
        scope_type: str = SCOPE_TYPE_GLOBAL,
        scope_id: str = SCOPE_ID_GLOBAL,
        limit: int = 10,
    ) -> list[MemoryItem]:
        """Search active public items within one exact scope."""
        fts_query = build_fts_query(query)
        if fts_query:
            rows = await self._repo.search_memory_items_public_scoped(
                fts_query,
                scope_type=scope_type,
                scope_id=scope_id,
                limit=limit,
            )
        else:
            rows = await self._repo.list_memory_items_public_scoped(
                scope_type=scope_type,
                scope_id=scope_id,
                limit=limit,
            )
        return [_row_to_item(row) for row in rows]

    async def list_public_items(
        self,
        *,
        scope_type: str = SCOPE_TYPE_GLOBAL,
        scope_id: str = SCOPE_ID_GLOBAL,
        limit: int = 40,
    ) -> list[MemoryItem]:
        """List active PUBLIC items for a scope (SQL-level sensitivity filter).

        The Markdown projection (the agent's grep-fallback recall surface) uses
        this so restricted items are excluded before the LIMIT — a heap of
        private/secret_like items can't starve public items out of the projection
        budget, and the leak fixed in ``96860d7`` is structurally impossible.
        """
        rows = await self._repo.list_memory_items_public_scoped(
            scope_type=scope_type,
            scope_id=scope_id,
            limit=limit,
        )
        return [_row_to_item(row) for row in rows]

    async def search_items_public_all_scopes(
        self,
        query: str = "",
        *,
        limit: int = 10,
    ) -> list[MemoryItem]:
        """Soft-scope cross-scope recall (Piece A2): public items, all scopes.

        Admits only ``sensitivity='public'`` items regardless of origin scope,
        enforced at the SQL layer by the repository. The retrieval adapter
        dedupes these against the in-scope cascade by ``item_id``.
        """
        fts_query = build_fts_query(query)
        if fts_query:
            rows = await self._repo.search_memory_items_public_all_scopes(
                fts_query, limit=limit
            )
        else:
            rows = await self._repo.list_memory_items_public_all_scopes(limit=limit)
        return [_row_to_item(row) for row in rows]

    async def append_candidate(
        self,
        *,
        candidate_id: str,
        content: str,
        title: str = "",
        scope_type: str = SCOPE_TYPE_GLOBAL,
        scope_id: str = SCOPE_ID_GLOBAL,
        kind: str = "fact",
        status: str = "pending",
        confidence: float = 0.5,
        evidence: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Store a memory consolidation candidate for audit/review."""
        return await self._repo.append_memory_candidate(
            candidate_id=candidate_id,
            scope_type=scope_type,
            scope_id=scope_id,
            kind=kind,
            title=title,
            content=content,
            status=status,
            confidence=confidence,
            evidence=evidence,
            metadata=metadata,
        )

    async def list_candidates(
        self,
        *,
        status: str | None = None,
        scope_type: str | None = SCOPE_TYPE_GLOBAL,
        scope_id: str | None = SCOPE_ID_GLOBAL,
        limit: int = 20,
    ) -> list[MemoryCandidate]:
        """List memory consolidation candidates."""
        rows = await self._repo.list_memory_candidates(
            status=status,
            scope_type=scope_type,
            scope_id=scope_id,
            limit=limit,
        )
        return [_row_to_candidate(row) for row in rows]

    async def mark_candidate_applied(self, candidate_id: str) -> bool:
        """Mark a consolidation candidate as applied."""
        return await self._repo.update_memory_candidate_status(
            candidate_id, "auto_applied"
        )

    async def upsert_item_embedding(
        self,
        item_id: str,
        embedding: list[float],
        *,
        provider_id: str,
        model: str,
        content_hash: str,
        vector_index: VectorIndex | None = None,
    ) -> str:
        """Persist an embedding for a memory item and optional vector index."""
        embedding_id = _embedding_id_for(
            item_id=item_id,
            provider_id=provider_id,
            model=model,
            content_hash=content_hash,
        )
        await self._repo.upsert_memory_embedding(
            embedding_id=embedding_id,
            item_id=item_id,
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
                        item_id=item_id,
                        embedding=embedding,
                    )
                ]
            )
        return embedding_id

    async def embed_items(
        self,
        provider: EmbeddingProvider,
        *,
        scope_type: str = SCOPE_TYPE_GLOBAL,
        scope_id: str = SCOPE_ID_GLOBAL,
        limit: int = 100,
        vector_index: VectorIndex | None = None,
    ) -> int:
        """Embed active memory items lacking a current embedding for this scope.

        Items whose current content already has a persisted vector for this
        provider+model are skipped, so the scheduled refresh only embeds new
        items instead of re-embedding the whole scope every run.
        """
        items = await self.search_items(
            "",
            scope_type=scope_type,
            scope_id=scope_id,
            limit=limit,
        )
        return await self._embed_pending_items(provider, items, vector_index)

    async def embed_items_all_scopes(
        self,
        provider: EmbeddingProvider,
        *,
        limit: int = 100,
        vector_index: VectorIndex | None = None,
    ) -> int:
        """Embed active memory items lacking a current embedding across all scopes.

        Covers chat-scoped items so vector/hybrid search over non-global scopes
        returns matches. Embeddings are scope-agnostic (derived from text), so a
        single pass over all active items is sufficient. Already-embedded items
        are skipped via ``content_hash`` dedup.
        """
        rows = await self._repo.list_memory_items_all_scopes(limit=limit)
        items = [_row_to_item(row) for row in rows]
        return await self._embed_pending_items(provider, items, vector_index)

    async def _embed_pending_items(
        self,
        provider: EmbeddingProvider,
        items: list[MemoryItem],
        vector_index: VectorIndex | None,
    ) -> int:
        """Embed only items lacking a current vector for this provider+model.

        Returns the number of new embeddings written this call. ``content_hash``
        dedup against ``list_memory_embedding_keys`` makes this idempotent across
        refreshes: items embedded in a previous run are skipped (no provider
        call), and a model switch re-embeds because the new model's keys are
        absent.
        """
        existing = await self._repo.list_memory_embedding_keys(
            provider_id=provider.provider_id,
            model=provider.model,
        )
        pending: list[tuple[MemoryItem, str]] = []
        for item in items:
            text = _item_embedding_text(item)
            if (item.item_id, memory_text_hash(text)) in existing:
                continue
            pending.append((item, text))
        if not pending:
            return 0
        results = await provider.embed_texts([text for _, text in pending])
        count = 0
        for (item, text), result in zip(pending, results, strict=False):
            if not result.embedding:
                continue
            await self.upsert_item_embedding(
                item.item_id,
                result.embedding,
                provider_id=result.provider_id,
                model=result.model,
                content_hash=memory_text_hash(text),
                vector_index=vector_index,
            )
            count += 1
        return count

    async def search_items_vector(
        self,
        query: str,
        provider: EmbeddingProvider,
        *,
        scope_type: str = SCOPE_TYPE_GLOBAL,
        scope_id: str = SCOPE_ID_GLOBAL,
        limit: int = 10,
        vector_index: VectorIndex | None = None,
    ) -> list[MemoryItem]:
        """Search memory items by cosine similarity over persisted embeddings."""
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
            rows = await self._repo.get_memory_items_by_ids(
                [hit.item_id for hit in hits]
            )
            items = [
                replace(
                    _row_to_item(row),
                    score=score_by_id.get(str(row["item_id"]), 0.0),
                )
                for row in rows
                if row.get("scope_type") == scope_type
                and row.get("scope_id") == scope_id
            ]
            return items[:limit]

        rows = await self._repo.list_memory_embeddings(
            provider_id=embedded[0].provider_id,
            model=embedded[0].model,
            dimensions=len(query_embedding),
            scope_type=scope_type,
            scope_id=scope_id,
        )
        embeddings = [_row_to_embedding(row) for row in rows]
        ranked = sorted(
            (
                (
                    embedding.item_id,
                    cosine_similarity(query_embedding, embedding.embedding),
                )
                for embedding in embeddings
            ),
            key=lambda item: item[1],
            reverse=True,
        )[:limit]
        rows_by_rank = await self._repo.get_memory_items_by_ids(
            [item_id for item_id, _score in ranked]
        )
        score_by_id = {item_id: score for item_id, score in ranked}
        return [
            replace(_row_to_item(row), score=score_by_id.get(str(row["item_id"]), 0.0))
            for row in rows_by_rank
        ]

    async def search_items_hybrid(
        self,
        query: str,
        provider: EmbeddingProvider | None = None,
        *,
        scope_type: str = SCOPE_TYPE_GLOBAL,
        scope_id: str = SCOPE_ID_GLOBAL,
        limit: int = 10,
        vector_index: VectorIndex | None = None,
    ) -> list[MemoryItem]:
        """Search memory items with FTS BM25 plus optional vector RRF fusion."""
        fts_items = await self.search_items(
            query,
            scope_type=scope_type,
            scope_id=scope_id,
            limit=limit,
        )
        if provider is None:
            return fts_items

        vector_items = await self.search_items_vector(
            query,
            provider,
            scope_type=scope_type,
            scope_id=scope_id,
            limit=limit,
            vector_index=vector_index,
        )
        if not vector_items:
            return fts_items
        if not fts_items:
            return vector_items

        fused = reciprocal_rank_fusion(
            [
                [item.item_id for item in fts_items],
                [item.item_id for item in vector_items],
            ],
            limit=limit,
        )
        rows = await self._repo.get_memory_items_by_ids(
            [item_id for item_id, _score in fused]
        )
        score_by_id = dict(fused)
        return [
            replace(_row_to_item(row), score=score_by_id.get(str(row["item_id"]), 0.0))
            for row in rows
        ]

    # ── Hierarchy (Phase 3b) ────────────────────────────────────

    async def get_children(
        self,
        parent_id: str,
        *,
        scope_type: str | None = None,
        scope_id: str | None = None,
        limit: int = 50,
    ) -> list[MemoryItem]:
        """Return direct children of a parent memory item."""
        rows = await self._repo.get_memory_children(
            parent_id,
            scope_type=scope_type,
            scope_id=scope_id,
            limit=limit,
        )
        return [_row_to_item(row) for row in rows]

    async def get_descendants(
        self,
        node_id: str,
        *,
        limit: int = 100,
    ) -> list[MemoryItem]:
        """Return a node and all its descendants."""
        rows = await self._repo.get_memory_descendants(node_id, limit=limit)
        return [_row_to_item(row) for row in rows]

    async def get_parents(self, node_id: str) -> list[MemoryItem]:
        """Walk up the parent chain for a memory item."""
        rows = await self._repo.get_memory_parents(node_id)
        return [_row_to_item(row) for row in rows]
