"""Bounded, read-only operator summaries of structured memory storage."""

from __future__ import annotations

from typing import Any

from nahida_bot.db.engine import DatabaseEngine


class SQLiteMemoryInspection:
    def __init__(self, engine: DatabaseEngine) -> None:
        self._engine = engine

    async def item(self, item_id: str) -> dict[str, Any] | None:
        """Operator inspection includes archived items; retrieval remains active-only."""
        from nahida_bot.db.repositories.sqlite_memory_repo import SQLiteMemoryRepository

        async with self._engine.write_lock:
            row = await self._engine.fetch_one(
                "SELECT *, 0.0 AS score FROM memory_items WHERE item_id=?", (item_id,)
            )
        return SQLiteMemoryRepository._memory_item_row_to_dict(row) if row else None

    async def overview(
        self, scope_type: str = "", scope_id: str = ""
    ) -> dict[str, Any]:
        conditions, params = [], []
        for column, value in (("scope_type", scope_type), ("scope_id", scope_id)):
            if value:
                conditions.append(f"{column} = ?")
                params.append(value)
        where = " AND ".join(conditions) or "1=1"
        metadata = (
            "CASE WHEN json_valid(metadata_json) THEN metadata_json ELSE '{}' END"
        )
        subject = f"json_extract({metadata}, '$.attribution.subject.kind')"
        # These counters describe recorded fields, not factual accuracy or visibility.
        async with self._engine.write_lock:
            counts = await self._engine.fetch_one(
                f"""SELECT COUNT(*) AS total,
                COUNT(CASE WHEN status='active' THEN 1 END) AS active,
                COUNT(CASE WHEN status='archived' THEN 1 END) AS archived,
                COUNT(CASE WHEN status='active' AND sensitivity!='public' THEN 1 END) AS restricted,
                COUNT(CASE WHEN status='active' AND COALESCE({subject},'unknown')='unknown' THEN 1 END) AS without_subject,
                COUNT(CASE WHEN status='active' AND COALESCE(json_array_length({metadata}, '$.attribution.evidence'),0)=0 THEN 1 END) AS without_evidence,
                MAX(updated_at) AS last_updated_at
                FROM memory_items WHERE {where}""",
                tuple(params),
            )
            candidates = await self._engine.fetch_all(
                f"SELECT status, COUNT(*) AS count FROM memory_candidates WHERE {where} GROUP BY status",
                tuple(params),
            )
            scopes = await self._engine.fetch_all(
                f"""SELECT scope_type, scope_id, COUNT(*) AS active,
                COUNT(CASE WHEN sensitivity!='public' THEN 1 END) AS restricted,
                MAX(updated_at) AS last_updated_at
                FROM memory_items WHERE ({where}) AND status='active'
                GROUP BY scope_type, scope_id
                ORDER BY active DESC, scope_type, scope_id LIMIT 51""",
                tuple(params),
            )
        return {
            **dict(counts or {}),
            "last_updated_at": counts["last_updated_at"] or "" if counts else "",
            "candidates": {row["status"]: row["count"] for row in candidates},
            "scopes": [dict(row) for row in scopes[:50]],
            "scopes_truncated": len(scopes) > 50,
        }
