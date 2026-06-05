"""SQLite repository for per-plugin key-value data store."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from nahida_bot.db.engine import DatabaseEngine


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SQLitePluginDataRepository:
    """Per-plugin key-value data store backed by the ``plugin_data`` table.

    Each plugin is isolated by ``plugin_id`` — callers must always supply it.
    Values are arbitrary JSON-serialisable objects stored as ``value_json``.
    """

    def __init__(self, engine: DatabaseEngine) -> None:
        self._engine = engine

    # ── Reads ─────────────────────────────────────────

    async def get(self, plugin_id: str, key: str) -> Any | None:
        """Return the parsed JSON value for *key*, or ``None`` if not found."""
        row = await self._engine.fetch_one(
            "SELECT value_json FROM plugin_data WHERE plugin_id = ? AND key = ?",
            (plugin_id, key),
        )
        if row is None:
            return None
        return json.loads(str(row["value_json"]))

    async def get_all(self, plugin_id: str) -> dict[str, Any]:
        """Return all key-value pairs for *plugin_id* as a dict."""
        rows = await self._engine.fetch_all(
            "SELECT key, value_json FROM plugin_data WHERE plugin_id = ? ORDER BY key",
            (plugin_id,),
        )
        return {str(row["key"]): json.loads(str(row["value_json"])) for row in rows}

    async def get_by_prefix(self, plugin_id: str, prefix: str) -> dict[str, Any]:
        """Return key-value pairs where the key starts with *prefix*."""
        rows = await self._engine.fetch_all(
            """
            SELECT key, value_json
            FROM plugin_data
            WHERE plugin_id = ? AND key LIKE ? ESCAPE '\\'
            ORDER BY key
            """,
            (plugin_id, _escape_like(prefix) + "%"),
        )
        return {str(row["key"]): json.loads(str(row["value_json"])) for row in rows}

    async def list_keys(self, plugin_id: str) -> list[str]:
        """Return all keys for *plugin_id* without fetching values."""
        rows = await self._engine.fetch_all(
            "SELECT key FROM plugin_data WHERE plugin_id = ? ORDER BY key",
            (plugin_id,),
        )
        return [str(row["key"]) for row in rows]

    # ── Writes ────────────────────────────────────────

    async def set(self, plugin_id: str, key: str, value: Any) -> None:
        """Upsert a value. Serialises *value* to JSON."""
        now = _utc_now_iso()
        value_json = json.dumps(value, ensure_ascii=False)
        async with self._engine.write_lock:
            await self._engine.execute(
                """
                INSERT INTO plugin_data (plugin_id, key, value_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(plugin_id, key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (plugin_id, key, value_json, now, now),
            )
            await self._engine.db.commit()

    async def delete(self, plugin_id: str, key: str) -> bool:
        """Hard-delete one key. Returns ``True`` if a row was removed."""
        async with self._engine.write_lock:
            cursor = await self._engine.execute(
                "DELETE FROM plugin_data WHERE plugin_id = ? AND key = ?",
                (plugin_id, key),
            )
            await self._engine.db.commit()
            return cursor.rowcount > 0

    async def delete_by_prefix(self, plugin_id: str, prefix: str) -> int:
        """Delete all keys matching *prefix*. Returns the number of rows removed."""
        async with self._engine.write_lock:
            cursor = await self._engine.execute(
                "DELETE FROM plugin_data WHERE plugin_id = ? AND key LIKE ? ESCAPE '\\'",
                (plugin_id, _escape_like(prefix) + "%"),
            )
            await self._engine.db.commit()
            return cursor.rowcount
