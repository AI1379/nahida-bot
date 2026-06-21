"""SQLite repository for observed chat/group names (find_chat support).

Stores the human-readable name of each typed chat the bot has seen, so the
agent can resolve a user's phrase ("原神群") to a ChatAddress
(``milky:group:20001``). Populated best-effort from inbound events at the
router (``core/router.py``); this is observe-only — no live channel list API
is queried. See ``docs/design/memory-architecture-exploration.md`` §8.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from nahida_bot.db.engine import DatabaseEngine


def _utc_now_iso() -> str:
    """Return the current UTC time as an aware ISO8601 string."""
    return datetime.now(UTC).isoformat()


def _like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class SQLiteChatMetadataRepository:
    """Typed SQLite data access for chat/group name observations."""

    def __init__(self, engine: DatabaseEngine) -> None:
        self._engine = engine

    async def observe(
        self,
        chat_address: str,
        *,
        platform: str = "",
        target_type: str = "",
        target_id: str = "",
        display_name: str,
    ) -> None:
        """Upsert a chat's observed name.

        On conflict (same ``chat_address``) refreshes ``display_name`` and
        ``last_seen_at``; ``first_seen_at`` is preserved. An empty
        ``display_name`` is a no-op — there is nothing to record.
        """
        if not display_name or not chat_address:
            return
        now_iso = _utc_now_iso()
        async with self._engine.write_lock:
            await self._engine.execute(
                "INSERT INTO chat_metadata (chat_address, platform, target_type, "
                "target_id, display_name, first_seen_at, last_seen_at, metadata_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, NULL) "
                "ON CONFLICT(chat_address) DO UPDATE SET "
                "platform = excluded.platform, "
                "target_type = excluded.target_type, "
                "target_id = excluded.target_id, "
                "display_name = excluded.display_name, "
                "last_seen_at = excluded.last_seen_at",
                (
                    chat_address,
                    platform,
                    target_type,
                    target_id,
                    display_name,
                    now_iso,
                    now_iso,
                ),
            )
            await self._engine.db.commit()

    async def search_by_name(
        self,
        query: str,
        *,
        platform: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Fuzzy-match chats by display name (case-insensitive substring).

        Returns rows ordered by most-recently-seen first. ``platform`` optional
        filter narrows to a single channel (e.g. ``"milky"``).
        """
        if not query:
            return []
        conditions = ["display_name LIKE ? ESCAPE '\\'"]
        params: list[Any] = [_like_pattern(query)]
        if platform:
            conditions.append("platform = ?")
            params.append(platform)
        params.append(limit)
        rows = await self._engine.fetch_all(
            "SELECT chat_address, platform, target_type, target_id, "
            "display_name, first_seen_at, last_seen_at "
            f"FROM chat_metadata WHERE {' AND '.join(conditions)} "
            "ORDER BY last_seen_at DESC, chat_address LIMIT ?",
            tuple(params),
        )
        return [dict(row) for row in rows]

    async def get(self, chat_address: str) -> dict[str, Any] | None:
        """Return the observed metadata for one chat, or ``None`` if unseen."""
        row = await self._engine.fetch_one(
            "SELECT chat_address, platform, target_type, target_id, "
            "display_name, first_seen_at, last_seen_at, metadata_json "
            "FROM chat_metadata WHERE chat_address = ?",
            (chat_address,),
        )
        if row is None:
            return None
        result = dict(row)
        raw_meta = result.pop("metadata_json", None)
        if raw_meta:
            try:
                result["metadata"] = json.loads(raw_meta)
            except (TypeError, ValueError):
                result["metadata"] = {}
        return result

    async def get_many(self, chat_addresses: list[str]) -> dict[str, str]:
        """Bulk-resolve chat names. Returns ``{chat_address: display_name}``.

        Used to annotate history-search results with friendly chat names when
        available; unseen addresses are simply absent from the map.
        """
        if not chat_addresses:
            return {}
        placeholders = ",".join("?" for _ in chat_addresses)
        rows = await self._engine.fetch_all(
            "SELECT chat_address, display_name FROM chat_metadata "
            f"WHERE chat_address IN ({placeholders})",
            tuple(chat_addresses),
        )
        return {row["chat_address"]: row["display_name"] for row in rows}
