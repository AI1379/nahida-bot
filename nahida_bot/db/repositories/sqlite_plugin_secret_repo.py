"""SQLite storage for plugin-owned credentials and other secrets."""

from __future__ import annotations

from datetime import UTC, datetime

from nahida_bot.db.engine import DatabaseEngine


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class SQLitePluginSecretRepository:
    """Per-plugin secret storage without enumeration or structured values.

    Values are intentionally opaque strings and are never exposed through the
    general plugin-data APIs. As with provider credentials, operators must
    protect the SQLite database with filesystem permissions.
    """

    def __init__(self, engine: DatabaseEngine) -> None:
        self._engine = engine

    async def get(self, plugin_id: str, key: str) -> str | None:
        row = await self._engine.fetch_one(
            "SELECT secret FROM plugin_secrets WHERE plugin_id = ? AND key = ?",
            (plugin_id, key),
        )
        return None if row is None else str(row["secret"])

    async def set(self, plugin_id: str, key: str, secret: str) -> None:
        now = _utc_now_iso()
        async with self._engine.write_lock:
            await self._engine.execute(
                """
                INSERT INTO plugin_secrets
                    (plugin_id, key, secret, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(plugin_id, key) DO UPDATE SET
                    secret = excluded.secret,
                    updated_at = excluded.updated_at
                """,
                (plugin_id, key, secret, now, now),
            )
            await self._engine.db.commit()

    async def delete(self, plugin_id: str, key: str) -> bool:
        async with self._engine.write_lock:
            cursor = await self._engine.execute(
                "DELETE FROM plugin_secrets WHERE plugin_id = ? AND key = ?",
                (plugin_id, key),
            )
            await self._engine.db.commit()
            return cursor.rowcount > 0
