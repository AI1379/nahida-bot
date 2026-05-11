"""SQLite database engine with async support."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import aiosqlite


_SCHEMA_MIGRATIONS = [
    # Migration 001: sessions, memory_turns, memory_keywords
    """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        workspace_id TEXT,
        created_at TEXT NOT NULL,
        last_active_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS memory_turns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT '',
        metadata_json TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (session_id) REFERENCES sessions(session_id)
    );

    CREATE TABLE IF NOT EXISTS memory_keywords (
        turn_id INTEGER NOT NULL,
        keyword TEXT NOT NULL,
        FOREIGN KEY (turn_id) REFERENCES memory_turns(id)
    );

    CREATE INDEX IF NOT EXISTS idx_keywords_keyword
        ON memory_keywords(keyword);

    CREATE INDEX IF NOT EXISTS idx_turns_session_created
        ON memory_turns(session_id, created_at);
    """,
    # Migration 002: add metadata column to sessions
    """
    ALTER TABLE sessions ADD COLUMN metadata_json TEXT;
    """,
    # Migration 003: cron scheduled jobs
    """
    CREATE TABLE IF NOT EXISTS cron_jobs (
        job_id TEXT PRIMARY KEY,
        platform TEXT NOT NULL,
        chat_id TEXT NOT NULL,
        session_key TEXT NOT NULL,
        prompt TEXT NOT NULL,
        mode TEXT NOT NULL,
        fire_at TEXT,
        interval_seconds INTEGER,
        max_runs INTEGER,
        run_count INTEGER NOT NULL DEFAULT 0,
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        next_fire_at TEXT NOT NULL,
        last_fired_at TEXT,
        workspace_id TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_cron_active
        ON cron_jobs(is_active, next_fire_at);
    """,
    # Migration 004: cron claim/failure tracking
    """
    ALTER TABLE cron_jobs ADD COLUMN claimed_at TEXT;
    ALTER TABLE cron_jobs ADD COLUMN failure_count INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE cron_jobs ADD COLUMN last_error TEXT;

    CREATE INDEX IF NOT EXISTS idx_cron_claimable
        ON cron_jobs(is_active, claimed_at, next_fire_at);
    """,
    # Migration 005: active session overrides (survives restart)
    """
    CREATE TABLE IF NOT EXISTS active_sessions (
        chat_key TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    # Migration 006: agent orchestration background task ledger
    """
    CREATE TABLE IF NOT EXISTS background_tasks (
        task_id TEXT PRIMARY KEY,
        runtime TEXT NOT NULL,
        status TEXT NOT NULL,
        requester_session_id TEXT NOT NULL,
        child_session_id TEXT,
        parent_task_id TEXT,
        title TEXT NOT NULL,
        summary TEXT NOT NULL DEFAULT '',
        delivery_target_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        ended_at TEXT,
        error TEXT NOT NULL DEFAULT ''
    );

    CREATE INDEX IF NOT EXISTS idx_background_tasks_requester
        ON background_tasks(requester_session_id, created_at);

    CREATE INDEX IF NOT EXISTS idx_background_tasks_status
        ON background_tasks(status, updated_at);
    """,
    # Migration 007: cron expression support
    """
    ALTER TABLE cron_jobs ADD COLUMN cron_expression TEXT;
    """,
    # Migration 008: structured memory items with FTS5/BM25 index
    """
    CREATE TABLE IF NOT EXISTS memory_items (
        item_id TEXT PRIMARY KEY,
        scope_type TEXT NOT NULL,
        scope_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        content TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        confidence REAL NOT NULL DEFAULT 1.0,
        importance REAL NOT NULL DEFAULT 0.5,
        sensitivity TEXT NOT NULL DEFAULT 'private',
        source TEXT NOT NULL DEFAULT 'plugin',
        evidence_json TEXT,
        metadata_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_memory_items_scope
        ON memory_items(scope_type, scope_id, status, updated_at);

    CREATE VIRTUAL TABLE IF NOT EXISTS memory_item_fts USING fts5(
        item_id UNINDEXED,
        scope_type UNINDEXED,
        scope_id UNINDEXED,
        title_index,
        content_index
    );

    CREATE TABLE IF NOT EXISTS memory_candidates (
        candidate_id TEXT PRIMARY KEY,
        scope_type TEXT NOT NULL,
        scope_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        content TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        confidence REAL NOT NULL DEFAULT 0.5,
        evidence_json TEXT,
        metadata_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_memory_candidates_scope
        ON memory_candidates(scope_type, scope_id, status, updated_at);
    """,
    # Migration 009: memory embeddings for vector and hybrid retrieval
    """
    CREATE TABLE IF NOT EXISTS memory_embeddings (
        embedding_id TEXT PRIMARY KEY,
        item_id TEXT NOT NULL,
        provider_id TEXT NOT NULL,
        model TEXT NOT NULL,
        dimensions INTEGER NOT NULL,
        content_hash TEXT NOT NULL,
        embedding_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (item_id) REFERENCES memory_items(item_id),
        UNIQUE(item_id, provider_id, model, content_hash)
    );

    CREATE INDEX IF NOT EXISTS idx_memory_embeddings_model
        ON memory_embeddings(provider_id, model, dimensions);

    CREATE INDEX IF NOT EXISTS idx_memory_embeddings_item
        ON memory_embeddings(item_id);
    """,
]


class DatabaseEngine:
    """Async SQLite engine with schema migration support.

    TODO: Add ``__aenter__`` / ``__aexit__`` so callers can use
    ``async with DatabaseEngine(...) as db:`` for guaranteed connection
    cleanup on exception paths. Currently callers must remember to call
    ``close()`` manually.
    """

    def __init__(self, db_path: str | Path) -> None:
        """Create engine for given database path.

        Args:
            db_path: File path or ``":memory:"`` for transient databases.
        """
        self._db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None
        self._write_lock: asyncio.Lock = asyncio.Lock()

    @property
    def db(self) -> aiosqlite.Connection:
        """Return the active database connection.

        Raises:
            RuntimeError: If called before ``initialize()``.
        """
        if self._db is None:
            raise RuntimeError("Database engine is not initialized")
        return self._db

    @property
    def write_lock(self) -> asyncio.Lock:
        """Lock for serializing write operations."""
        return self._write_lock

    async def initialize(self) -> None:
        """Open the database connection and run pending migrations."""
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._run_migrations()

    async def close(self) -> None:
        """Close the database connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def execute(
        self, sql: str, parameters: tuple[Any, ...] | None = None
    ) -> aiosqlite.Cursor:
        """Execute a single SQL statement."""
        return await self.db.execute(sql, parameters or ())

    async def fetch_one(
        self, sql: str, parameters: tuple[Any, ...] | None = None
    ) -> aiosqlite.Row | None:
        """Execute a query and return the first row, or None."""
        cursor = await self.db.execute(sql, parameters or ())
        return await cursor.fetchone()

    async def fetch_all(
        self, sql: str, parameters: tuple[Any, ...] | None = None
    ) -> list[aiosqlite.Row]:
        """Execute a query and return all matching rows."""
        cursor = await self.db.execute(sql, parameters or ())
        return list(await cursor.fetchall())

    async def _run_migrations(self) -> None:
        """Apply pending schema migrations with version tracking."""
        await self.db.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
        )
        await self.db.commit()

        cursor = await self.db.execute("SELECT version FROM schema_version")
        row = await cursor.fetchone()
        current_version = int(row["version"]) if row else 0

        for idx, migration_sql in enumerate(_SCHEMA_MIGRATIONS, start=1):
            if idx <= current_version:
                continue
            await self.db.executescript(migration_sql)

        new_version = len(_SCHEMA_MIGRATIONS)
        if new_version > current_version:
            if current_version == 0:
                await self.db.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (new_version,),
                )
            else:
                await self.db.execute(
                    "UPDATE schema_version SET version = ?",
                    (new_version,),
                )
            await self.db.commit()
