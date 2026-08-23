"""SQLite data access for generic document collections."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from nahida_bot.db.engine import DatabaseEngine


def _utc_now_iso() -> str:
    """Return the current UTC time as an aware ISO8601 string."""
    return datetime.now(UTC).isoformat()


def _safe_collection_name(name: str) -> str:
    """Validate a collection name is safe for use in table names.

    Only alphanumeric characters and underscores are allowed.
    This prevents SQL injection via collection names.
    """
    if not name:
        raise ValueError("collection name must not be empty")
    if not all(c.isalnum() or c == "_" for c in name):
        raise ValueError(
            f"collection name must contain only alphanumeric characters and "
            f"underscores, got: {name!r}"
        )
    return name


class SQLiteDocumentRepository:
    """Typed SQLite data access for document collections.

    Each instance is bound to a single collection.  All table names are
    derived from the collection name with a ``_docs``, ``_doc_fts``, and
    ``_doc_embeddings`` suffix.

    Parameters
    ----------
    engine:
        The shared database engine.
    collection:
        Collection name.  Used as a table name prefix (validated to be
        alphanumeric + underscore).
    """

    def __init__(self, engine: DatabaseEngine, *, collection: str) -> None:
        self._engine = engine
        self._collection = _safe_collection_name(collection)
        self._docs_table = f"{self._collection}_docs"
        self._fts_table = f"{self._collection}_doc_fts"
        self._emb_table = f"{self._collection}_doc_embeddings"

    @property
    def collection(self) -> str:
        return self._collection

    @property
    def docs_table(self) -> str:
        return self._docs_table

    @property
    def fts_table(self) -> str:
        return self._fts_table

    @property
    def embeddings_table(self) -> str:
        return self._emb_table

    # ── Schema ────────────────────────────────────────────

    async def create_tables(self) -> None:
        """Create all tables and indexes for this collection.

        Safe to call multiple times (uses ``IF NOT EXISTS``).
        """
        async with self._engine.write_lock:
            await self._engine.execute(
                f"CREATE TABLE IF NOT EXISTS {self._docs_table} ("
                "doc_id TEXT PRIMARY KEY, "
                "title TEXT NOT NULL DEFAULT '', "
                "content TEXT NOT NULL, "
                "status TEXT NOT NULL DEFAULT 'active', "
                "metadata_json TEXT DEFAULT '{}', "
                "created_at TEXT NOT NULL, "
                "updated_at TEXT NOT NULL, "
                "retrieval_text TEXT NOT NULL DEFAULT '', "
                "path TEXT NOT NULL DEFAULT '', "
                "source_id TEXT NOT NULL DEFAULT '', "
                "chunk_index INTEGER NOT NULL DEFAULT 0, "
                "parent_id TEXT NOT NULL DEFAULT '', "
                "root_id TEXT NOT NULL DEFAULT '', "
                "node_type TEXT NOT NULL DEFAULT 'passage'"
                ")"
            )
            await self._engine.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS {self._fts_table} USING fts5("
                "doc_id UNINDEXED, "
                "title_index, "
                "content_index"
                ")"
            )
            await self._engine.execute(
                f"CREATE TABLE IF NOT EXISTS {self._emb_table} ("
                "embedding_id TEXT PRIMARY KEY, "
                f"doc_id TEXT NOT NULL REFERENCES {self._docs_table}(doc_id) ON DELETE CASCADE, "
                "provider_id TEXT NOT NULL, "
                "model TEXT NOT NULL, "
                "dimensions INTEGER NOT NULL, "
                "content_hash TEXT NOT NULL, "
                "embedding_json TEXT NOT NULL, "
                "created_at TEXT NOT NULL, "
                "UNIQUE(doc_id, provider_id, model, content_hash)"
                ")"
            )
            await self._engine.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{self._collection}_emb_model "
                f"ON {self._emb_table}(provider_id, model, dimensions)"
            )
            await self._engine.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{self._collection}_emb_doc "
                f"ON {self._emb_table}(doc_id)"
            )
            await self._engine.db.commit()
        # Add Phase-1 columns to pre-existing {collection}_docs tables (created
        # before retrieval_text/path/source_id/chunk_index existed). Idempotent.
        # MUST run before the (source_id, chunk_index) index below — otherwise
        # old tables without those columns fail the CREATE INDEX with
        # "no such column: source_id".
        await self._ensure_docs_columns()
        async with self._engine.write_lock:
            await self._engine.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{self._collection}_docs_source "
                f"ON {self._docs_table}(source_id, chunk_index)"
            )
            await self._engine.db.commit()

    async def drop_tables(self) -> None:
        """Drop all tables for this collection."""
        async with self._engine.write_lock:
            await self._engine.execute(f"DROP TABLE IF EXISTS {self._emb_table}")
            await self._engine.execute(f"DROP TABLE IF EXISTS {self._fts_table}")
            await self._engine.execute(f"DROP TABLE IF EXISTS {self._docs_table}")
            await self._engine.db.commit()

    async def _ensure_docs_columns(self) -> None:
        """Add Phase-1 columns to pre-existing ``{collection}_docs`` tables.

        New tables get the columns from ``CREATE TABLE``; this only ``ALTER``s
        columns missing on tables created before Phase 1. Existing rows get the
        column defaults (empty retrieval_text/path/source_id, 0 chunk_index) and
        keep working — the store falls back to title+content for FTS/embedding
        when retrieval_text is empty, so old collections degrade gracefully
        until re-imported rather than breaking.
        """
        rows = await self._engine.fetch_all(f"PRAGMA table_info({self._docs_table})")
        existing = {str(row["name"]) for row in rows}
        additions = [
            ("retrieval_text", "TEXT NOT NULL DEFAULT ''"),
            ("path", "TEXT NOT NULL DEFAULT ''"),
            ("source_id", "TEXT NOT NULL DEFAULT ''"),
            ("chunk_index", "INTEGER NOT NULL DEFAULT 0"),
            ("parent_id", "TEXT NOT NULL DEFAULT ''"),
            ("root_id", "TEXT NOT NULL DEFAULT ''"),
            ("node_type", "TEXT NOT NULL DEFAULT 'passage'"),
        ]
        async with self._engine.write_lock:
            for name, decl in additions:
                if name not in existing:
                    await self._engine.execute(
                        f"ALTER TABLE {self._docs_table} ADD COLUMN {name} {decl}"
                    )
            await self._engine.db.commit()

    # ── Document CRUD ─────────────────────────────────────

    async def insert_document(
        self,
        *,
        doc_id: str,
        title: str,
        content: str,
        metadata: dict[str, Any] | None,
        title_index: str,
        content_index: str,
        retrieval_text: str = "",
        path: str = "",
        source_id: str = "",
        chunk_index: int = 0,
        parent_id: str = "",
        root_id: str = "",
        node_type: str = "passage",
    ) -> str:
        """Insert or update a document and replace its FTS row."""
        now_iso = _utc_now_iso()
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        async with self._engine.write_lock:
            await self._engine.execute(
                f"INSERT INTO {self._docs_table} "
                "(doc_id, title, content, status, metadata_json, created_at, "
                "updated_at, retrieval_text, path, source_id, chunk_index, "
                "parent_id, root_id, node_type) "
                "VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(doc_id) DO UPDATE SET "
                "title = excluded.title, "
                "content = excluded.content, "
                "status = excluded.status, "
                "metadata_json = excluded.metadata_json, "
                "retrieval_text = excluded.retrieval_text, "
                "path = excluded.path, "
                "source_id = excluded.source_id, "
                "chunk_index = excluded.chunk_index, "
                "parent_id = excluded.parent_id, "
                "root_id = excluded.root_id, "
                "node_type = excluded.node_type, "
                "updated_at = excluded.updated_at",
                (
                    doc_id,
                    title,
                    content,
                    metadata_json,
                    now_iso,
                    now_iso,
                    retrieval_text,
                    path,
                    source_id,
                    chunk_index,
                    parent_id,
                    root_id,
                    node_type,
                ),
            )
            await self._engine.execute(
                f"DELETE FROM {self._fts_table} WHERE doc_id = ?",
                (doc_id,),
            )
            await self._engine.execute(
                f"INSERT INTO {self._fts_table} "
                "(doc_id, title_index, content_index) "
                "VALUES (?, ?, ?)",
                (doc_id, title_index, content_index),
            )
            await self._engine.db.commit()
        return doc_id

    async def get_document(self, doc_id: str) -> dict[str, Any] | None:
        """Retrieve a single document by ID.  Returns ``None`` if not found."""
        row = await self._engine.fetch_one(
            f"SELECT doc_id, title, content, status, metadata_json, "
            f"retrieval_text, path, source_id, chunk_index, "
            f"parent_id, root_id, node_type, "
            f"created_at, updated_at FROM {self._docs_table} "
            "WHERE doc_id = ?",
            (doc_id,),
        )
        if row is None:
            return None
        return self._doc_row_to_dict(row)

    async def delete_document(self, doc_id: str) -> bool:
        """Delete a document and its FTS row.  Returns ``True`` if it existed."""
        async with self._engine.write_lock:
            cursor = await self._engine.execute(
                f"DELETE FROM {self._docs_table} WHERE doc_id = ?",
                (doc_id,),
            )
            # FTS rows are auto-cleaned if the content table uses an external
            # content table, but our FTS table is standalone, so delete manually.
            await self._engine.execute(
                f"DELETE FROM {self._fts_table} WHERE doc_id = ?",
                (doc_id,),
            )
            await self._engine.db.commit()
        return cursor.rowcount > 0

    async def count_documents(self) -> int:
        """Return the number of active documents in the collection."""
        row = await self._engine.fetch_one(
            f"SELECT COUNT(*) AS count FROM {self._docs_table} WHERE status = 'active'"
        )
        if row is None:
            return 0
        return int(row["count"])

    async def search_documents_fts(
        self,
        fts_query: str,
        *,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search documents via FTS5 BM25 ranking.

        Scores are reported as ``-bm25()`` — positive, larger-is-better — so
        every retrieval mode (FTS / RRF / vector) shares one score direction
        and a ``min_score`` threshold means the same thing everywhere. FTS5's
        raw ``bm25()`` is negative with *more negative = better*, which used
        to invert threshold semantics and drop the strongest matches
        (issue #49).

        Structural nodes (``document`` / ``section``) are excluded: their
        content is just the heading/file name, and BM25 length normalization
        makes those ~5-char nodes dominate any query that touches their
        terms. They remain stored for ``context_read`` tree expansion — only
        retrieval ranking skips them.
        """
        rows = await self._engine.fetch_all(
            f"SELECT d.doc_id, d.title, d.content, d.status, d.metadata_json, "
            f"d.retrieval_text, d.path, d.source_id, d.chunk_index, "
            f"d.parent_id, d.root_id, d.node_type, "
            f"d.created_at, d.updated_at, -bm25({self._fts_table}) AS score "
            f"FROM {self._fts_table} "
            f"JOIN {self._docs_table} d ON d.doc_id = {self._fts_table}.doc_id "
            f"WHERE {self._fts_table} MATCH ? "
            "AND d.status = 'active' "
            "AND d.node_type = 'passage' "
            "ORDER BY score DESC, d.updated_at DESC "
            "LIMIT ?",
            (fts_query, limit),
        )
        return [self._doc_row_to_dict(row) for row in rows]

    async def get_neighbors(
        self,
        source_id: str,
        *,
        chunk_index: int,
        before: int = 1,
        after: int = 1,
    ) -> list[dict[str, Any]]:
        """Return sibling chunks of one chunk within the same source document.

        Used for neighbor expansion: given a hit, pull its immediately adjacent
        chunks (by ``chunk_index``) so the caller can show surrounding context.
        Ordered by ``chunk_index`` ascending.
        """
        rows = await self._engine.fetch_all(
            f"SELECT doc_id, title, content, status, metadata_json, "
            f"retrieval_text, path, source_id, chunk_index, "
            f"parent_id, root_id, node_type, "
            f"created_at, updated_at, 0.0 AS score "
            f"FROM {self._docs_table} "
            "WHERE source_id = ? AND status = 'active' "
            "AND chunk_index BETWEEN ? AND ? "
            "AND chunk_index != ? "
            "ORDER BY chunk_index ASC",
            (source_id, chunk_index - before, chunk_index + after, chunk_index),
        )
        return [self._doc_row_to_dict(row) for row in rows]

    async def get_children(
        self,
        parent_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return direct children of a parent, ordered by chunk_index."""
        rows = await self._engine.fetch_all(
            f"SELECT doc_id, title, content, status, metadata_json, "
            f"retrieval_text, path, source_id, chunk_index, "
            f"parent_id, root_id, node_type, "
            f"created_at, updated_at, 0.0 AS score "
            f"FROM {self._docs_table} "
            "WHERE parent_id = ? AND status = 'active' "
            "ORDER BY chunk_index ASC "
            "LIMIT ?",
            (parent_id, max(1, limit)),
        )
        return [self._doc_row_to_dict(row) for row in rows]

    async def get_subtree(
        self,
        root_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return all active nodes under a root, ordered by path + chunk_index."""
        rows = await self._engine.fetch_all(
            f"SELECT doc_id, title, content, status, metadata_json, "
            f"retrieval_text, path, source_id, chunk_index, "
            f"parent_id, root_id, node_type, "
            f"created_at, updated_at, 0.0 AS score "
            f"FROM {self._docs_table} "
            "WHERE root_id = ? AND status = 'active' "
            "ORDER BY path ASC, chunk_index ASC "
            "LIMIT ?",
            (root_id, max(1, limit)),
        )
        return [self._doc_row_to_dict(row) for row in rows]

    async def get_descendants(
        self,
        node_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return a node and all its descendants via recursive parent walk.

        Uses a recursive CTE to follow ``parent_id`` links downward from
        ``node_id``. The starting node itself is included. Results are
        ordered by path + chunk_index for natural document order.
        """
        rows = await self._engine.fetch_all(
            f"WITH RECURSIVE subtree AS ("
            f"  SELECT doc_id, title, content, status, metadata_json, "
            f"  retrieval_text, path, source_id, chunk_index, "
            f"  parent_id, root_id, node_type, "
            f"  created_at, updated_at "
            f"  FROM {self._docs_table} "
            f"  WHERE doc_id = ? "
            f"  UNION ALL "
            f"  SELECT d.doc_id, d.title, d.content, d.status, d.metadata_json, "
            f"  d.retrieval_text, d.path, d.source_id, d.chunk_index, "
            f"  d.parent_id, d.root_id, d.node_type, "
            f"  d.created_at, d.updated_at "
            f"  FROM {self._docs_table} d "
            f"  JOIN subtree s ON d.parent_id = s.doc_id "
            f") "
            f"SELECT * FROM subtree "
            f"WHERE status = 'active' "
            f"ORDER BY path ASC, chunk_index ASC "
            f"LIMIT ?",
            (node_id, max(1, limit)),
        )
        return [self._doc_row_to_dict(row) for row in rows]

    async def get_parents(self, node_id: str) -> list[dict[str, Any]]:
        """Walk up the parent chain for a node, root last.

        Returns an empty list when the node is not found. An item with
        ``parent_id = ''`` terminates the walk. The walk is structural — it
        follows ``parent_id`` regardless of ``status``, so a chain is not
        silently truncated at a soft-deleted ancestor (the caller can filter on
        ``status`` if it wants only active ancestors).
        """
        result: list[dict[str, Any]] = []
        current_id = node_id
        for _ in range(20):  # safety cap
            row = await self._engine.fetch_one(
                f"SELECT doc_id, title, content, status, metadata_json, "
                f"retrieval_text, path, source_id, chunk_index, "
                f"parent_id, root_id, node_type, "
                f"created_at, updated_at, 0.0 AS score "
                f"FROM {self._docs_table} "
                "WHERE doc_id = ?",
                (current_id,),
            )
            if row is None:
                break
            result.append(self._doc_row_to_dict(row))
            parent = str(row["parent_id"] or "")
            if not parent:
                break
            current_id = parent
        return result

    async def list_documents(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List recent active documents with pagination."""
        rows = await self._engine.fetch_all(
            f"SELECT doc_id, title, content, status, metadata_json, "
            f"retrieval_text, path, source_id, chunk_index, "
            f"parent_id, root_id, node_type, "
            f"created_at, updated_at, 0.0 AS score "
            f"FROM {self._docs_table} "
            "WHERE status = 'active' "
            "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [self._doc_row_to_dict(row) for row in rows]

    async def get_documents_by_ids(self, doc_ids: list[str]) -> list[dict[str, Any]]:
        """Return active documents by id, preserving input order."""
        if not doc_ids:
            return []
        placeholders = ",".join("?" for _ in doc_ids)
        rows = await self._engine.fetch_all(
            f"SELECT doc_id, title, content, status, metadata_json, "
            f"retrieval_text, path, source_id, chunk_index, "
            f"parent_id, root_id, node_type, "
            f"created_at, updated_at, 0.0 AS score "
            f"FROM {self._docs_table} "
            f"WHERE status = 'active' AND doc_id IN ({placeholders})",
            tuple(doc_ids),
        )
        by_id = {row["doc_id"]: self._doc_row_to_dict(row) for row in rows}
        return [by_id[did] for did in doc_ids if did in by_id]

    # ── Embeddings ────────────────────────────────────────

    async def upsert_embedding(
        self,
        *,
        embedding_id: str,
        doc_id: str,
        provider_id: str,
        model: str,
        dimensions: int,
        content_hash: str,
        embedding: list[float],
    ) -> str:
        """Insert or update a persisted embedding."""
        now_iso = _utc_now_iso()
        embedding_json = json.dumps(embedding, ensure_ascii=False)
        async with self._engine.write_lock:
            await self._engine.execute(
                f"INSERT INTO {self._emb_table} "
                "(embedding_id, doc_id, provider_id, model, dimensions, "
                "content_hash, embedding_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(doc_id, provider_id, model, content_hash) "
                "DO UPDATE SET embedding_id = excluded.embedding_id, "
                "embedding_json = excluded.embedding_json, "
                "dimensions = excluded.dimensions, "
                "content_hash = excluded.content_hash, "
                "created_at = excluded.created_at",
                (
                    embedding_id,
                    doc_id,
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

    async def list_embedding_ids_for_doc(
        self,
        doc_id: str,
        *,
        provider_id: str = "",
        model: str = "",
    ) -> list[str]:
        """Return embedding ids for one document, optionally filtered by model."""
        conditions = ["doc_id = ?"]
        params: list[Any] = [doc_id]
        if provider_id:
            conditions.append("provider_id = ?")
            params.append(provider_id)
        if model:
            conditions.append("model = ?")
            params.append(model)
        rows = await self._engine.fetch_all(
            f"SELECT embedding_id FROM {self._emb_table} "
            f"WHERE {' AND '.join(conditions)} ORDER BY created_at DESC",
            tuple(params),
        )
        return [str(row["embedding_id"]) for row in rows]

    async def list_embeddings(
        self,
        *,
        provider_id: str,
        model: str,
        dimensions: int,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List embeddings for active documents.

        ``offset`` pages through the result set so callers can stream a full
        index rebuild in bounded-memory batches (the JSON payloads are large).
        """
        rows = await self._engine.fetch_all(
            f"SELECT e.embedding_id, e.doc_id, e.provider_id, e.model, "
            f"e.dimensions, e.content_hash, e.embedding_json, e.created_at "
            f"FROM {self._emb_table} e "
            f"JOIN {self._docs_table} d ON d.doc_id = e.doc_id "
            "WHERE d.status = 'active' "
            "AND e.provider_id = ? AND e.model = ? AND e.dimensions = ? "
            "ORDER BY e.embedding_id ASC LIMIT ? OFFSET ?",
            (provider_id, model, dimensions, limit, offset),
        )
        return [
            {
                "embedding_id": str(row["embedding_id"]),
                "doc_id": str(row["doc_id"]),
                "provider_id": str(row["provider_id"]),
                "model": str(row["model"]),
                "dimensions": int(row["dimensions"]),
                "content_hash": str(row["content_hash"]),
                "embedding": json.loads(str(row["embedding_json"])),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    async def list_embedding_keys(
        self,
        *,
        provider_id: str,
        model: str,
    ) -> set[tuple[str, str]]:
        """Return persisted ``(doc_id, content_hash)`` keys for a provider+model.

        Used to skip documents whose current content already has a vector for
        this provider and model, so backfill is incremental instead of
        re-embedding every document on every call (and after every restart).
        Keys are not filtered to active documents on purpose: a soft-deleted
        document's stale key simply never matches a live document id.
        """
        rows = await self._engine.fetch_all(
            f"SELECT doc_id, content_hash FROM {self._emb_table} "
            "WHERE provider_id = ? AND model = ?",
            (provider_id, model),
        )
        return {(str(row["doc_id"]), str(row["content_hash"])) for row in rows}

    async def list_all_embedding_ids(
        self,
        *,
        provider_id: str,
        model: str,
    ) -> list[str]:
        """Return every embedding id persisted for a provider+model.

        The authoritative id set used to reconcile the derived vector index:
        rows present in the index but missing here are stale and get deleted;
        a count mismatch triggers a replay of these rows into the index.
        """
        rows = await self._engine.fetch_all(
            f"SELECT embedding_id FROM {self._emb_table} "
            "WHERE provider_id = ? AND model = ?",
            (provider_id, model),
        )
        return [str(row["embedding_id"]) for row in rows]

    async def delete_embeddings(self, embedding_ids: list[str]) -> int:
        """Delete embeddings by their ids."""
        if not embedding_ids:
            return 0
        placeholders = ",".join("?" for _ in embedding_ids)
        async with self._engine.write_lock:
            cursor = await self._engine.execute(
                f"DELETE FROM {self._emb_table} WHERE embedding_id IN ({placeholders})",
                tuple(embedding_ids),
            )
            await self._engine.db.commit()
        return cursor.rowcount

    async def delete_embeddings_for_doc(self, doc_id: str) -> int:
        """Delete all embeddings for a document."""
        async with self._engine.write_lock:
            cursor = await self._engine.execute(
                f"DELETE FROM {self._emb_table} WHERE doc_id = ?",
                (doc_id,),
            )
            await self._engine.db.commit()
        return cursor.rowcount

    # ── Helpers ───────────────────────────────────────────

    @staticmethod
    def _doc_row_to_dict(row: Any) -> dict[str, Any]:
        """Parse a document row, deserializing metadata_json.

        Accepts both ``dict`` and ``sqlite3.Row`` objects.
        """

        def _get(key: str, default: str = "") -> str:
            try:
                val = row[key]
            except (KeyError, IndexError):
                return default
            return val if val is not None else default

        metadata_raw = _get("metadata_json")
        metadata: dict[str, Any] = {}
        if metadata_raw:
            try:
                metadata = json.loads(str(metadata_raw))
            except (json.JSONDecodeError, TypeError):
                metadata = {}
        return {
            "doc_id": str(_get("doc_id")),
            "title": str(_get("title")),
            "content": str(_get("content")),
            "status": str(_get("status", "active")),
            "metadata": metadata,
            "created_at": str(_get("created_at")),
            "updated_at": str(_get("updated_at")),
            "retrieval_text": str(_get("retrieval_text")),
            "path": str(_get("path")),
            "source_id": str(_get("source_id")),
            "chunk_index": int(_get("chunk_index", "0") or 0),
            "parent_id": str(_get("parent_id")),
            "root_id": str(_get("root_id")),
            "node_type": str(_get("node_type") or "passage"),
            "score": float(_get("score", "0.0")),
        }
