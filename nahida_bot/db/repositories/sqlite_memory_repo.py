"""SQLite memory repository for conversation turn persistence."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from nahida_bot.db.engine import DatabaseEngine


def _utc_now_iso() -> str:
    """Return the current UTC time as an aware ISO8601 string."""
    return datetime.now(UTC).isoformat()


class SQLiteMemoryRepository:
    """Typed SQLite data access for session and conversation turn storage."""

    def __init__(self, engine: DatabaseEngine) -> None:
        self._engine = engine

    async def ensure_session(
        self, session_id: str, workspace_id: str | None = None
    ) -> None:
        """Insert a session row if it does not exist, refresh last_active_at."""
        now_iso = _utc_now_iso()
        async with self._engine.write_lock:
            await self._engine.execute(
                "INSERT INTO sessions (session_id, workspace_id, created_at, last_active_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET last_active_at = excluded.last_active_at",
                (session_id, workspace_id, now_iso, now_iso),
            )
            await self._engine.db.commit()

    async def append_turn(
        self,
        session_id: str,
        *,
        role: str,
        content: str,
        source: str = "",
        metadata: dict[str, Any] | None = None,
        keywords: list[str] | None = None,
    ) -> int:
        """Store a conversation turn and return its auto-generated id."""
        now_iso = _utc_now_iso()
        metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None

        async with self._engine.write_lock:
            cursor = await self._engine.execute(
                "INSERT INTO memory_turns "
                "(session_id, role, content, source, metadata_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, role, content, source, metadata_json, now_iso),
            )
            turn_id: int = cursor.lastrowid  # type: ignore[assignment]

            if keywords:
                await self._insert_keywords(turn_id, keywords)

            await self._engine.db.commit()
        return turn_id

    async def get_recent_turns(
        self, session_id: str, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Return recent turns for a session, newest last (chronological order)."""
        rows = await self._engine.fetch_all(
            "SELECT id, session_id, role, content, source, metadata_json, created_at "
            "FROM memory_turns "
            "WHERE session_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (session_id, limit),
        )
        return [self._row_to_dict(row) for row in reversed(rows)]

    async def search_by_keyword(
        self, session_id: str, keyword: str, *, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Search turns by keyword match within a session."""
        rows = await self._engine.fetch_all(
            "SELECT DISTINCT t.id, t.session_id, t.role, t.content, "
            "t.source, t.metadata_json, t.created_at "
            "FROM memory_turns t "
            "JOIN memory_keywords mk ON mk.turn_id = t.id "
            "WHERE t.session_id = ? AND mk.keyword = ? "
            "ORDER BY t.created_at DESC LIMIT ?",
            (session_id, keyword.lower(), limit),
        )
        return [self._row_to_dict(row) for row in rows]

    async def search_by_keywords(
        self, session_id: str, keywords: list[str], *, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Search turns matching *any* of the given keywords (OR), ranked by match count."""
        if not keywords:
            return []
        placeholders = ",".join("?" for _ in keywords)
        params: list[Any] = [session_id] + [kw.lower() for kw in keywords] + [limit]
        rows = await self._engine.fetch_all(
            "SELECT t.id, t.session_id, t.role, t.content, "
            "t.source, t.metadata_json, t.created_at, "
            "COUNT(DISTINCT mk.keyword) AS match_count "
            "FROM memory_turns t "
            "JOIN memory_keywords mk ON mk.turn_id = t.id "
            "WHERE t.session_id = ? AND mk.keyword IN (" + placeholders + ") "
            "GROUP BY t.id "
            "ORDER BY match_count DESC, t.created_at DESC LIMIT ?",
            tuple(params),
        )
        return [self._row_to_dict(row) for row in rows]

    async def get_keywords_for_turn(self, turn_id: int) -> list[str]:
        """Return all indexed keywords for a given turn."""
        rows = await self._engine.fetch_all(
            "SELECT keyword FROM memory_keywords WHERE turn_id = ?",
            (turn_id,),
        )
        return [row["keyword"] for row in rows]

    async def get_keywords_for_turns(self, turn_ids: list[int]) -> dict[int, list[str]]:
        """Bulk-fetch keywords for multiple turns."""
        if not turn_ids:
            return {}
        placeholders = ",".join("?" for _ in turn_ids)
        rows = await self._engine.fetch_all(
            "SELECT turn_id, keyword FROM memory_keywords "
            "WHERE turn_id IN (" + placeholders + ")",
            tuple(turn_ids),
        )
        result: dict[int, list[str]] = {tid: [] for tid in turn_ids}
        for row in rows:
            result[row["turn_id"]].append(row["keyword"])
        return result

    async def search_by_time_window(
        self,
        session_id: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Search turns within a time window for a session."""
        conditions = ["session_id = ?"]
        params: list[Any] = [session_id]

        if since is not None:
            conditions.append("created_at >= ?")
            params.append(since.isoformat())
        if until is not None:
            conditions.append("created_at <= ?")
            params.append(until.isoformat())

        where_clause = " AND ".join(conditions)
        sql = (
            "SELECT id, session_id, role, content, source, metadata_json, created_at "
            f"FROM memory_turns WHERE {where_clause} "
            "ORDER BY created_at DESC LIMIT ?"
        )
        params.append(limit)

        rows = await self._engine.fetch_all(sql, tuple(params))
        return [self._row_to_dict(row) for row in reversed(rows)]

    async def delete_turns_before(self, cutoff: datetime) -> int:
        """Delete turns older than cutoff. Returns count of deleted rows."""
        cutoff_iso = cutoff.isoformat()
        async with self._engine.write_lock:
            # Delete keywords first (no ON DELETE CASCADE on the FK).
            await self._engine.execute(
                "DELETE FROM memory_keywords WHERE turn_id IN ("
                "SELECT id FROM memory_turns WHERE created_at < ?"
                ")",
                (cutoff_iso,),
            )
            cursor = await self._engine.execute(
                "DELETE FROM memory_turns WHERE created_at < ?",
                (cutoff_iso,),
            )
            await self._engine.db.commit()
        return cursor.rowcount

    async def clear_session_turns(self, session_id: str) -> int:
        """Delete all turns and keywords for a session. Returns deleted turn count."""
        async with self._engine.write_lock:
            await self._engine.execute(
                "DELETE FROM memory_keywords WHERE turn_id IN ("
                "SELECT id FROM memory_turns WHERE session_id = ?"
                ")",
                (session_id,),
            )
            cursor = await self._engine.execute(
                "DELETE FROM memory_turns WHERE session_id = ?",
                (session_id,),
            )
            await self._engine.db.commit()
        return cursor.rowcount

    async def list_sessions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """List sessions with turn counts."""
        rows = await self._engine.fetch_all(
            "SELECT s.session_id, s.workspace_id, s.created_at, "
            "s.last_active_at, s.metadata_json, "
            "(SELECT COUNT(*) FROM memory_turns t WHERE t.session_id = s.session_id) AS turn_count "
            "FROM sessions s ORDER BY s.last_active_at DESC LIMIT ?",
            (limit,),
        )
        results: list[dict[str, Any]] = []
        for row in rows:
            d: dict[str, Any] = dict(row)
            metadata_raw = d.pop("metadata_json", None)
            if isinstance(metadata_raw, str):
                try:
                    d["metadata"] = json.loads(metadata_raw)
                except (json.JSONDecodeError, ValueError):
                    d["metadata"] = {}
            else:
                d["metadata"] = {}
            results.append(d)
        return results

    async def get_session_metadata(self, session_id: str) -> dict[str, Any]:
        """Get session metadata_json as a dict."""
        row = await self._engine.fetch_one(
            "SELECT metadata_json FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        if row is None:
            return {}
        raw = row["metadata_json"]
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                return {}
        return {}

    async def update_session_metadata(
        self, session_id: str, updates: dict[str, Any]
    ) -> None:
        """Merge updates into session metadata_json (upsert)."""
        existing = await self.get_session_metadata(session_id)
        merged = {**existing, **updates}
        merged_json = json.dumps(merged, ensure_ascii=False)
        async with self._engine.write_lock:
            await self._engine.execute(
                "UPDATE sessions SET metadata_json = ? WHERE session_id = ?",
                (merged_json, session_id),
            )
            await self._engine.db.commit()

    # -- Active session overrides --

    async def set_active_session(self, chat_key: str, session_id: str) -> None:
        """Persist the active session override for a chat."""
        now_iso = _utc_now_iso()
        async with self._engine.write_lock:
            await self._engine.execute(
                "INSERT INTO active_sessions (chat_key, session_id, updated_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(chat_key) DO UPDATE SET "
                "session_id = excluded.session_id, updated_at = excluded.updated_at",
                (chat_key, session_id, now_iso),
            )
            await self._engine.db.commit()

    async def get_active_session(self, chat_key: str) -> str | None:
        """Return the persisted session override for a chat, or None."""
        row = await self._engine.fetch_one(
            "SELECT session_id FROM active_sessions WHERE chat_key = ?",
            (chat_key,),
        )
        if row is not None:
            return str(row["session_id"])
        return None

    async def load_all_active_sessions(self) -> dict[str, str]:
        """Return all persisted session overrides as {chat_key: session_id}."""
        rows = await self._engine.fetch_all(
            "SELECT chat_key, session_id FROM active_sessions"
        )
        return {str(row["chat_key"]): str(row["session_id"]) for row in rows}

    # -- Durable memory items --

    async def append_memory_item(
        self,
        *,
        item_id: str,
        scope_type: str,
        scope_id: str,
        kind: str,
        title: str,
        content: str,
        status: str,
        confidence: float,
        importance: float,
        sensitivity: str,
        source: str,
        evidence: dict[str, Any] | None,
        metadata: dict[str, Any] | None,
        title_index: str,
        content_index: str,
    ) -> str:
        """Store a durable memory item and its FTS index row."""
        now_iso = _utc_now_iso()
        evidence_json = json.dumps(evidence, ensure_ascii=False) if evidence else None
        metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
        async with self._engine.write_lock:
            await self._engine.execute(
                "INSERT INTO memory_items "
                "(item_id, scope_type, scope_id, kind, title, content, status, "
                "confidence, importance, sensitivity, source, evidence_json, "
                "metadata_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item_id,
                    scope_type,
                    scope_id,
                    kind,
                    title,
                    content,
                    status,
                    confidence,
                    importance,
                    sensitivity,
                    source,
                    evidence_json,
                    metadata_json,
                    now_iso,
                    now_iso,
                ),
            )
            await self._engine.execute(
                "INSERT INTO memory_item_fts "
                "(item_id, scope_type, scope_id, title_index, content_index) "
                "VALUES (?, ?, ?, ?, ?)",
                (item_id, scope_type, scope_id, title_index, content_index),
            )
            await self._engine.db.commit()
        return item_id

    async def search_memory_items(
        self,
        fts_query: str,
        *,
        scope_type: str,
        scope_id: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search durable memory items via FTS5 BM25 ranking."""
        rows = await self._engine.fetch_all(
            "SELECT mi.item_id, mi.scope_type, mi.scope_id, mi.kind, mi.title, "
            "mi.content, mi.status, mi.confidence, mi.importance, mi.sensitivity, "
            "mi.source, mi.evidence_json, mi.metadata_json, mi.created_at, "
            "mi.updated_at, bm25(memory_item_fts) AS score "
            "FROM memory_item_fts "
            "JOIN memory_items mi ON mi.item_id = memory_item_fts.item_id "
            "WHERE memory_item_fts MATCH ? "
            "AND mi.status = 'active' "
            "AND mi.scope_type = ? "
            "AND mi.scope_id = ? "
            "ORDER BY score ASC, mi.importance DESC, mi.updated_at DESC "
            "LIMIT ?",
            (fts_query, scope_type, scope_id, limit),
        )
        return [self._memory_item_row_to_dict(row) for row in rows]

    async def list_memory_items(
        self,
        *,
        scope_type: str,
        scope_id: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """List recent active durable memory items for a scope."""
        rows = await self._engine.fetch_all(
            "SELECT item_id, scope_type, scope_id, kind, title, content, status, "
            "confidence, importance, sensitivity, source, evidence_json, "
            "metadata_json, created_at, updated_at, 0.0 AS score "
            "FROM memory_items "
            "WHERE status = 'active' AND scope_type = ? AND scope_id = ? "
            "ORDER BY importance DESC, updated_at DESC LIMIT ?",
            (scope_type, scope_id, limit),
        )
        return [self._memory_item_row_to_dict(row) for row in rows]

    async def archive_memory_item(self, item_id: str) -> bool:
        """Mark a durable memory item as archived."""
        now_iso = _utc_now_iso()
        async with self._engine.write_lock:
            cursor = await self._engine.execute(
                "UPDATE memory_items SET status = 'archived', updated_at = ? "
                "WHERE item_id = ? AND status != 'archived'",
                (now_iso, item_id),
            )
            await self._engine.db.commit()
        return cursor.rowcount > 0

    async def get_memory_items_by_ids(
        self,
        item_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Return active durable memory items by id."""
        if not item_ids:
            return []
        placeholders = ",".join("?" for _ in item_ids)
        rows = await self._engine.fetch_all(
            "SELECT item_id, scope_type, scope_id, kind, title, content, status, "
            "confidence, importance, sensitivity, source, evidence_json, "
            "metadata_json, created_at, updated_at, 0.0 AS score "
            f"FROM memory_items WHERE status = 'active' AND item_id IN ({placeholders})",
            tuple(item_ids),
        )
        by_id = {row["item_id"]: self._memory_item_row_to_dict(row) for row in rows}
        return [by_id[item_id] for item_id in item_ids if item_id in by_id]

    async def upsert_memory_embedding(
        self,
        *,
        embedding_id: str,
        item_id: str,
        provider_id: str,
        model: str,
        dimensions: int,
        content_hash: str,
        embedding: list[float],
    ) -> str:
        """Insert or update a persisted memory embedding."""
        now_iso = _utc_now_iso()
        embedding_json = json.dumps(embedding, ensure_ascii=False)
        async with self._engine.write_lock:
            await self._engine.execute(
                "INSERT INTO memory_embeddings "
                "(embedding_id, item_id, provider_id, model, dimensions, "
                "content_hash, embedding_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(item_id, provider_id, model, content_hash) "
                "DO UPDATE SET embedding_id = excluded.embedding_id, "
                "embedding_json = excluded.embedding_json, "
                "dimensions = excluded.dimensions, "
                "created_at = excluded.created_at",
                (
                    embedding_id,
                    item_id,
                    provider_id,
                    model,
                    dimensions,
                    content_hash,
                    embedding_json,
                    now_iso,
                ),
            )
            await self._engine.db.commit()
        return embedding_id

    async def list_memory_embeddings(
        self,
        *,
        provider_id: str,
        model: str,
        dimensions: int,
        scope_type: str,
        scope_id: str,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """List embeddings for active memory items in a scope."""
        rows = await self._engine.fetch_all(
            "SELECT me.embedding_id, me.item_id, me.provider_id, me.model, "
            "me.dimensions, me.content_hash, me.embedding_json, me.created_at "
            "FROM memory_embeddings me "
            "JOIN memory_items mi ON mi.item_id = me.item_id "
            "WHERE mi.status = 'active' "
            "AND mi.scope_type = ? AND mi.scope_id = ? "
            "AND me.provider_id = ? AND me.model = ? AND me.dimensions = ? "
            "ORDER BY me.created_at DESC LIMIT ?",
            (scope_type, scope_id, provider_id, model, dimensions, limit),
        )
        return [self._memory_embedding_row_to_dict(row) for row in rows]

    async def delete_memory_embeddings_for_item(self, item_id: str) -> int:
        """Delete persisted embeddings for one memory item."""
        async with self._engine.write_lock:
            cursor = await self._engine.execute(
                "DELETE FROM memory_embeddings WHERE item_id = ?",
                (item_id,),
            )
            await self._engine.db.commit()
        return cursor.rowcount

    # -- Memory consolidation candidates --

    async def append_memory_candidate(
        self,
        *,
        candidate_id: str,
        scope_type: str,
        scope_id: str,
        kind: str,
        title: str,
        content: str,
        status: str,
        confidence: float,
        evidence: dict[str, Any] | None,
        metadata: dict[str, Any] | None,
    ) -> str:
        """Store a consolidation candidate for audit/review."""
        now_iso = _utc_now_iso()
        evidence_json = json.dumps(evidence, ensure_ascii=False) if evidence else None
        metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
        async with self._engine.write_lock:
            await self._engine.execute(
                "INSERT INTO memory_candidates "
                "(candidate_id, scope_type, scope_id, kind, title, content, status, "
                "confidence, evidence_json, metadata_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    candidate_id,
                    scope_type,
                    scope_id,
                    kind,
                    title,
                    content,
                    status,
                    confidence,
                    evidence_json,
                    metadata_json,
                    now_iso,
                    now_iso,
                ),
            )
            await self._engine.db.commit()
        return candidate_id

    async def list_memory_candidates(
        self,
        *,
        status: str | None = None,
        scope_type: str = "global",
        scope_id: str = "__global__",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List consolidation candidates."""
        params: list[Any] = [scope_type, scope_id]
        status_filter = ""
        if status:
            status_filter = "AND status = ? "
            params.append(status)
        params.append(limit)
        rows = await self._engine.fetch_all(
            "SELECT candidate_id, scope_type, scope_id, kind, title, content, "
            "status, confidence, evidence_json, metadata_json, created_at, "
            "updated_at FROM memory_candidates "
            "WHERE scope_type = ? AND scope_id = ? "
            f"{status_filter}"
            "ORDER BY updated_at DESC LIMIT ?",
            tuple(params),
        )
        return [self._memory_candidate_row_to_dict(row) for row in rows]

    async def update_memory_candidate_status(
        self,
        candidate_id: str,
        status: str,
    ) -> bool:
        """Update a consolidation candidate status."""
        now_iso = _utc_now_iso()
        async with self._engine.write_lock:
            cursor = await self._engine.execute(
                "UPDATE memory_candidates SET status = ?, updated_at = ? "
                "WHERE candidate_id = ?",
                (status, now_iso, candidate_id),
            )
            await self._engine.db.commit()
        return cursor.rowcount > 0

    async def _insert_keywords(self, turn_id: int, keywords: list[str]) -> None:
        """Insert keyword associations for a turn.

        TODO: This issues N individual INSERT statements. For CJK text jieba
        can produce 20-50+ keywords per turn. Replace with ``executemany`` or
        a single multi-value INSERT to reduce round-trips.
        """
        for keyword in keywords:
            await self._engine.execute(
                "INSERT INTO memory_keywords (turn_id, keyword) VALUES (?, ?)",
                (turn_id, keyword.lower()),
            )

    @staticmethod
    def _row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:  # type: ignore[name-defined]
        """Convert a database row to a plain dict with parsed metadata."""
        data: dict[str, Any] = dict(row)
        metadata_json = data.get("metadata_json")
        if isinstance(metadata_json, str):
            try:
                data["metadata"] = json.loads(metadata_json)
            except (json.JSONDecodeError, ValueError):
                data["metadata"] = None
        else:
            data["metadata"] = None
        data.pop("metadata_json", None)
        data.pop("match_count", None)
        return data

    @staticmethod
    def _memory_item_row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
        """Convert a memory item row into a plain dict with parsed JSON fields."""
        data: dict[str, Any] = dict(row)
        evidence_raw = data.pop("evidence_json", None)
        metadata_raw = data.pop("metadata_json", None)
        if isinstance(evidence_raw, str):
            try:
                data["evidence"] = json.loads(evidence_raw)
            except (json.JSONDecodeError, ValueError):
                data["evidence"] = {}
        else:
            data["evidence"] = {}
        if isinstance(metadata_raw, str):
            try:
                data["metadata"] = json.loads(metadata_raw)
            except (json.JSONDecodeError, ValueError):
                data["metadata"] = {}
        else:
            data["metadata"] = {}
        return data

    @staticmethod
    def _memory_embedding_row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
        """Convert a memory embedding row into a dict with parsed vector JSON."""
        data: dict[str, Any] = dict(row)
        embedding_raw = data.pop("embedding_json", None)
        if isinstance(embedding_raw, str):
            try:
                parsed = json.loads(embedding_raw)
            except (json.JSONDecodeError, ValueError):
                parsed = []
        else:
            parsed = []
        data["embedding"] = [
            float(value) for value in parsed if isinstance(value, int | float)
        ]
        return data

    @staticmethod
    def _memory_candidate_row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
        """Convert a memory candidate row into a plain dict."""
        data: dict[str, Any] = dict(row)
        evidence_raw = data.pop("evidence_json", None)
        metadata_raw = data.pop("metadata_json", None)
        if isinstance(evidence_raw, str):
            try:
                data["evidence"] = json.loads(evidence_raw)
            except (json.JSONDecodeError, ValueError):
                data["evidence"] = {}
        else:
            data["evidence"] = {}
        if isinstance(metadata_raw, str):
            try:
                data["metadata"] = json.loads(metadata_raw)
            except (json.JSONDecodeError, ValueError):
                data["metadata"] = {}
        else:
            data["metadata"] = {}
        return data
