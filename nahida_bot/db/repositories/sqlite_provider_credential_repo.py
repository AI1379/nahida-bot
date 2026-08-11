"""SQLite storage for provider credentials managed by the auth CLI."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from nahida_bot.db.engine import DatabaseEngine


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True, frozen=True)
class ProviderCredential:
    """One stored provider credential.

    ``secret`` is intentionally never included in CLI list output or logs.
    SQLite offers the same at-rest posture as the existing Codex refresh-token
    table; operators must protect the database file with filesystem permissions.
    """

    provider_id: str
    auth_method: str
    secret: str
    updated_at: str = ""


class SQLiteProviderCredentialRepository:
    """CRUD for API keys and future provider auth methods."""

    def __init__(self, engine: DatabaseEngine) -> None:
        self._engine = engine

    async def get(self, provider_id: str) -> ProviderCredential | None:
        row = await self._engine.fetch_one(
            "SELECT provider_id, auth_method, secret, updated_at "
            "FROM provider_credentials WHERE provider_id = ?",
            (provider_id,),
        )
        if row is None:
            return None
        return ProviderCredential(
            provider_id=str(row["provider_id"]),
            auth_method=str(row["auth_method"]),
            secret=str(row["secret"]),
            updated_at=str(row["updated_at"]),
        )

    async def upsert(self, credential: ProviderCredential) -> None:
        now = _utc_now_iso()
        async with self._engine.write_lock:
            await self._engine.execute(
                """
                INSERT INTO provider_credentials
                    (provider_id, auth_method, secret, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(provider_id) DO UPDATE SET
                    auth_method = excluded.auth_method,
                    secret = excluded.secret,
                    updated_at = excluded.updated_at
                """,
                (
                    credential.provider_id,
                    credential.auth_method,
                    credential.secret,
                    now,
                    now,
                ),
            )
            await self._engine.db.commit()

    async def delete(self, provider_id: str) -> bool:
        async with self._engine.write_lock:
            cursor = await self._engine.execute(
                "DELETE FROM provider_credentials WHERE provider_id = ?",
                (provider_id,),
            )
            await self._engine.db.commit()
            return cursor.rowcount > 0

    async def list_all(self) -> list[ProviderCredential]:
        rows = await self._engine.fetch_all(
            "SELECT provider_id, auth_method, secret, updated_at "
            "FROM provider_credentials ORDER BY provider_id"
        )
        return [
            ProviderCredential(
                provider_id=str(row["provider_id"]),
                auth_method=str(row["auth_method"]),
                secret=str(row["secret"]),
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        ]


def stored_provider_ids(db_path: str | Path) -> frozenset[str]:
    """Read stored API-key provider ids for synchronous CLI preflight checks."""
    path = str(db_path)
    if path == ":memory:" or not Path(path).is_file():
        return frozenset()
    try:
        connection = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
        try:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'provider_credentials'"
            ).fetchone()
            if exists is None:
                return frozenset()
            rows = connection.execute(
                "SELECT provider_id FROM provider_credentials "
                "WHERE auth_method = 'api_key' AND secret <> ''"
            ).fetchall()
            return frozenset(str(row[0]) for row in rows)
        finally:
            connection.close()
    except sqlite3.Error:
        return frozenset()
