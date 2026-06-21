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
    # Migration 010: cron session_mode (main vs isolated)
    """
    ALTER TABLE cron_jobs ADD COLUMN session_mode TEXT NOT NULL DEFAULT 'main';
    """,
    # Migration 011: chat_type column and session key migration audit log
    """
    ALTER TABLE cron_jobs ADD COLUMN chat_type TEXT NOT NULL DEFAULT '';

    CREATE TABLE IF NOT EXISTS session_key_migration_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        old_session_id TEXT NOT NULL,
        new_session_id TEXT,
        status TEXT NOT NULL,
        reason TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """,
    # Migration 012: session_name column for named cron session mode
    """
    ALTER TABLE cron_jobs ADD COLUMN session_name TEXT DEFAULT NULL;
    """,
    # Migration 013: outbound message delivery audit and cron ownership
    """
    CREATE TABLE IF NOT EXISTS message_deliveries (
        delivery_id TEXT PRIMARY KEY,
        target_chat_address TEXT NOT NULL,
        platform TEXT NOT NULL DEFAULT '',
        target_type TEXT NOT NULL DEFAULT '',
        target_id TEXT NOT NULL DEFAULT '',
        source_session_id TEXT NOT NULL DEFAULT '',
        source_chat_address TEXT NOT NULL DEFAULT '',
        source_user_id TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT '',
        delivery_mode TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT '',
        message_id TEXT NOT NULL DEFAULT '',
        text TEXT NOT NULL DEFAULT '',
        error TEXT NOT NULL DEFAULT '',
        metadata_json TEXT,
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_message_deliveries_target_created
        ON message_deliveries(target_chat_address, created_at);

    CREATE INDEX IF NOT EXISTS idx_message_deliveries_source_session_created
        ON message_deliveries(source_session_id, created_at);

    CREATE INDEX IF NOT EXISTS idx_message_deliveries_source_created
        ON message_deliveries(source, created_at);

    ALTER TABLE cron_jobs ADD COLUMN created_by_user_id TEXT NOT NULL DEFAULT '';
    ALTER TABLE cron_jobs ADD COLUMN created_from_session_id TEXT NOT NULL DEFAULT '';
    ALTER TABLE cron_jobs ADD COLUMN created_from_chat_address TEXT NOT NULL DEFAULT '';
    """,
    # Migration 014: token usage event ledger
    """
    CREATE TABLE IF NOT EXISTS usage_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        session_id TEXT NOT NULL DEFAULT '',
        source_tag TEXT NOT NULL DEFAULT '',
        provider_id TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        cached_tokens INTEGER NOT NULL DEFAULT 0,
        reasoning_tokens INTEGER NOT NULL DEFAULT 0,
        cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
        estimated INTEGER NOT NULL DEFAULT 0,
        estimated_cost REAL
    );

    CREATE INDEX IF NOT EXISTS idx_usage_timestamp
        ON usage_events(timestamp);

    CREATE INDEX IF NOT EXISTS idx_usage_provider
        ON usage_events(provider_id);

    CREATE INDEX IF NOT EXISTS idx_usage_session
        ON usage_events(session_id);
    """,
    # Migration 015: plugin key-value data store
    """
    CREATE TABLE IF NOT EXISTS plugin_data (
        plugin_id  TEXT    NOT NULL,
        key        TEXT    NOT NULL,
        value_json TEXT    NOT NULL DEFAULT '{}',
        created_at TEXT    NOT NULL,
        updated_at TEXT    NOT NULL,
        PRIMARY KEY (plugin_id, key)
    );

    CREATE INDEX IF NOT EXISTS idx_plugin_data_plugin
        ON plugin_data(plugin_id);
    """,
    # Migration 016: person/account identity (issue #7, Phase 0+1)
    """
    CREATE TABLE IF NOT EXISTS persons (
        person_id     TEXT    PRIMARY KEY,
        display_name  TEXT    NOT NULL DEFAULT '',
        status        TEXT    NOT NULL DEFAULT 'active',
        metadata_json TEXT    NOT NULL DEFAULT '{}',
        created_at    TEXT    NOT NULL,
        updated_at    TEXT    NOT NULL
    );

    CREATE TABLE IF NOT EXISTS person_accounts (
        account_key         TEXT    PRIMARY KEY,
        person_id           TEXT    NOT NULL REFERENCES persons(person_id) ON DELETE CASCADE,
        channel             TEXT    NOT NULL,
        account_type        TEXT    NOT NULL DEFAULT 'user',
        platform_account_id TEXT    NOT NULL,
        label               TEXT    NOT NULL DEFAULT '',
        status              TEXT    NOT NULL DEFAULT 'active',
        verification        TEXT    NOT NULL DEFAULT 'manual_link',
        linked_by           TEXT    NOT NULL DEFAULT '',
        linked_at           TEXT    NOT NULL,
        metadata_json       TEXT    NOT NULL DEFAULT '{}'
    );

    CREATE INDEX IF NOT EXISTS idx_person_accounts_person
        ON person_accounts(person_id, status);

    -- At most one active binding per (channel, account_type, platform_account_id).
    CREATE UNIQUE INDEX IF NOT EXISTS idx_person_accounts_unique_active
        ON person_accounts(channel, account_type, platform_account_id)
        WHERE status = 'active';

    CREATE TABLE IF NOT EXISTS account_observations (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        account_key     TEXT    NOT NULL,
        chat_address    TEXT    NOT NULL,
        display_name    TEXT    NOT NULL DEFAULT '',
        role_tags_json  TEXT,
        first_seen_at   TEXT    NOT NULL,
        last_seen_at    TEXT    NOT NULL,
        last_message_id TEXT    NOT NULL DEFAULT '',
        metadata_json   TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_account_observations_account
        ON account_observations(account_key, last_seen_at);

    CREATE INDEX IF NOT EXISTS idx_account_observations_chat
        ON account_observations(chat_address, last_seen_at);

    -- One row per (account, chat) so observation writes upsert on conflict.
    CREATE UNIQUE INDEX IF NOT EXISTS idx_account_observations_unique
        ON account_observations(account_key, chat_address);
    """,
    # Migration 017: memory tree columns (Phase 3b).
    # The ALTERs + index are applied idempotently by _ensure_memory_tree_columns
    # (run after _run_migrations) instead of inline here. Pure-SQL ALTER TABLE
    # has no IF NOT EXISTS and executescript() is not atomic (it commits before
    # running and isn't wrapped in a transaction), so an inline multi-ALTER
    # script would wedge the DB on a partial run: a crash after the first ALTER
    # leaves schema_version unchanged, and the next boot re-runs the script and
    # raises "duplicate column name". This no-op keeps the version count at 17
    # (no downgrade for DBs already at 17) while the columns are added safely.
    "SELECT 1;",
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

        # Phase-3b memory-tree columns are added here (idempotent) rather than
        # in migration 017 — see the comment on that migration entry.
        await self._ensure_memory_tree_columns()

    async def _ensure_memory_tree_columns(self) -> None:
        """Idempotently add the memory-tree columns + index to ``memory_items``.

        Each ``ALTER`` is guarded by ``PRAGMA table_info`` so a partial / crashed
        prior run is recoverable: only the missing columns are added, never
        raising "duplicate column name". The index uses ``IF NOT EXISTS``.
        """
        rows = await self.fetch_all("PRAGMA table_info(memory_items)")
        existing = {str(row["name"]) for row in rows}
        additions = [
            ("parent_id", "TEXT NOT NULL DEFAULT ''"),
            ("root_id", "TEXT NOT NULL DEFAULT ''"),
            ("node_type", "TEXT NOT NULL DEFAULT 'leaf'"),
            ("path", "TEXT NOT NULL DEFAULT ''"),
            ("source_id", "TEXT NOT NULL DEFAULT ''"),
        ]
        async with self.write_lock:
            for name, decl in additions:
                if name not in existing:
                    await self.db.execute(
                        f"ALTER TABLE memory_items ADD COLUMN {name} {decl}"
                    )
            await self.db.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_items_tree "
                "ON memory_items(parent_id, root_id)"
            )
            await self.db.commit()
