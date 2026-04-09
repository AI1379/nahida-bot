"""SQLite database engine with async support."""

from __future__ import annotations

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
]


class DatabaseEngine:
    """Async SQLite engine with schema migration support."""

    def __init__(self, db_path: str | Path) -> None:
        """Create engine for given database path.

        Args:
            db_path: File path or ``":memory:"`` for transient databases.
        """
        self._db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None

    @property
    def db(self) -> aiosqlite.Connection:
        """Return the active database connection.

        Raises:
            RuntimeError: If called before ``initialize()``.
        """
        if self._db is None:
            raise RuntimeError("Database engine is not initialized")
        return self._db

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
        """Apply all schema migrations in order."""
        for migration_sql in _SCHEMA_MIGRATIONS:
            await self.db.executescript(migration_sql)
        await self.db.commit()
