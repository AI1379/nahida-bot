"""SQLite-backed memory store implementation."""

from __future__ import annotations

import re
import warnings
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

# FIXME: jieba 0.42.1 emits SyntaxWarning on Python 3.12+ due to invalid escapes.
# Keep this suppression until we upgrade/patch jieba in a dedicated follow-up.
# TODO: jieba's dictionary loading costs ~0.5-1s at import time. Even if the
# memory subsystem is never used, this module-level import pays that cost.
# Consider lazy-loading: defer ``import jieba`` into ``extract_keywords()``
# on first call, or use ``functools.lru_cache`` on a wrapper.
with warnings.catch_warnings():
    warnings.simplefilter("ignore", SyntaxWarning)
    import jieba

from nahida_bot.agent.memory.models import (
    ConversationTurn,
    MemoryCandidate,
    MemoryEmbedding,
    MemoryItem,
    MemoryRecord,
    SessionSummary,
)
from nahida_bot.agent.memory.embedding import EmbeddingProvider, memory_text_hash
from nahida_bot.agent.memory.store import MemoryStore
from nahida_bot.agent.memory.vector import (
    VectorIndex,
    VectorRecord,
    cosine_similarity,
    reciprocal_rank_fusion,
)
from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.db.repositories.sqlite_memory_repo import SQLiteMemoryRepository

_MIN_KEYWORD_LENGTH = 2
_CJK_RANGE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uac00-\ud7af]")
_KEYWORD_SPLIT = re.compile(r"[^\w]+", re.UNICODE)
_FTS_SPECIAL = re.compile(r'["\s]+')


def extract_keywords(text: str, *, min_length: int = _MIN_KEYWORD_LENGTH) -> list[str]:
    """Extract normalized keywords from text for indexing.

    Uses jieba for CJK segmentation and whitespace splitting for Latin text.
    Preserves first-occurrence order with stable deduplication.
    """
    if not text:
        return []

    has_cjk = bool(_CJK_RANGE.search(text))

    if has_cjk:
        # jieba cut_for_search produces fine-grained tokens suitable for indexing.
        raw_tokens = jieba.lcut_for_search(text)
    else:
        raw_tokens = _KEYWORD_SPLIT.split(text.lower())

    seen: set[str] = set()
    result: list[str] = []
    for token in raw_tokens:
        token = token.strip().lower()
        if len(token) >= min_length and token not in seen:
            seen.add(token)
            result.append(token)
    return result


def tokenize_for_fts(text: str) -> str:
    """Tokenize text into a space-separated FTS index string.

    SQLite FTS5's BM25 ranking is useful, but its default tokenizer is not a
    Chinese segmenter. We pre-tokenize CJK text with jieba search mode and store
    the resulting tokens as an ASCII-space-separated index field.
    """
    return " ".join(extract_keywords(text))


def build_fts_query(query: str) -> str:
    """Build a safe OR query for pre-tokenized FTS fields."""
    tokens = extract_keywords(query)
    quoted: list[str] = []
    for token in tokens:
        cleaned = _FTS_SPECIAL.sub(" ", token).strip()
        if cleaned:
            quoted.append(f'"{cleaned}"')
    return " OR ".join(quoted)


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
        sensitivity=str(row.get("sensitivity", "private")),
        source=str(row.get("source", "plugin")),
        evidence=evidence if isinstance(evidence, dict) else {},
        metadata=metadata if isinstance(metadata, dict) else {},
        created_at=created_at,
        updated_at=updated_at,
        score=float(row.get("score", 0.0)),
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
        scope_type: str = "global",
        scope_id: str = "__global__",
        kind: str = "fact",
        source: str = "plugin",
        confidence: float = 1.0,
        importance: float = 0.5,
        sensitivity: str = "private",
        evidence: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        item_id: str | None = None,
    ) -> str:
        """Store a durable structured memory item and update the FTS index."""
        memory_id = item_id or f"mem_{uuid4().hex}"
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
            sensitivity=sensitivity,
            source=source,
            evidence=evidence,
            metadata=metadata,
            title_index=tokenize_for_fts(title),
            content_index=tokenize_for_fts(content),
        )
        return memory_id

    async def search_items(
        self,
        query: str = "",
        *,
        scope_type: str = "global",
        scope_id: str = "__global__",
        limit: int = 10,
    ) -> list[MemoryItem]:
        """Search durable memory items using FTS5 BM25 over pre-tokenized text."""
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

    async def append_candidate(
        self,
        *,
        candidate_id: str,
        content: str,
        title: str = "",
        scope_type: str = "global",
        scope_id: str = "__global__",
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
        scope_type: str = "global",
        scope_id: str = "__global__",
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
        scope_type: str = "global",
        scope_id: str = "__global__",
        limit: int = 100,
        vector_index: VectorIndex | None = None,
    ) -> int:
        """Embed recent active memory items for a scope."""
        items = await self.search_items(
            "",
            scope_type=scope_type,
            scope_id=scope_id,
            limit=limit,
        )
        texts = [_item_embedding_text(item) for item in items]
        results = await provider.embed_texts(texts)
        count = 0
        for item, text, result in zip(items, texts, results, strict=False):
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
        scope_type: str = "global",
        scope_id: str = "__global__",
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
        scope_type: str = "global",
        scope_id: str = "__global__",
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
