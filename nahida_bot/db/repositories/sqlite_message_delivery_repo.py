"""SQLite repository for outbound message delivery audit records."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import aiosqlite

from nahida_bot.db.engine import DatabaseEngine


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True, frozen=True)
class MessageDelivery:
    """One outbound delivery audit record."""

    delivery_id: str = ""
    target_chat_address: str = ""
    platform: str = ""
    target_type: str = ""
    target_id: str = ""
    source_session_id: str = ""
    source_chat_address: str = ""
    source_user_id: str = ""
    source: str = ""
    delivery_mode: str = ""
    status: str = ""
    message_id: str = ""
    text: str = ""
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""


@dataclass(slots=True, frozen=True)
class MessageDeliveryGroup:
    """Aggregated delivery records for one target chat address."""

    target_chat_address: str
    platform: str
    target_type: str
    target_id: str
    count: int
    last_created_at: str
    last_source: str = ""


class SQLiteMessageDeliveryStore:
    """Persist and query outbound message delivery audit data."""

    def __init__(self, engine: DatabaseEngine) -> None:
        self._engine = engine

    async def record(
        self,
        *,
        target_chat_address: str,
        platform: str = "",
        target_type: str = "",
        target_id: str = "",
        source_session_id: str = "",
        source_chat_address: str = "",
        source_user_id: str = "",
        source: str = "",
        delivery_mode: str = "",
        status: str = "sent",
        message_id: str = "",
        text: str = "",
        error: str = "",
        metadata: dict[str, Any] | None = None,
        created_at: str = "",
    ) -> MessageDelivery:
        """Insert a delivery record and return the stored object."""
        delivery = MessageDelivery(
            delivery_id=uuid4().hex,
            target_chat_address=target_chat_address,
            platform=platform,
            target_type=target_type,
            target_id=target_id,
            source_session_id=source_session_id,
            source_chat_address=source_chat_address,
            source_user_id=source_user_id,
            source=source,
            delivery_mode=delivery_mode,
            status=status,
            message_id=message_id,
            text=text,
            error=error,
            metadata=dict(metadata or {}),
            created_at=created_at or _utc_now_iso(),
        )
        await self.insert(delivery)
        return delivery

    async def insert(self, delivery: MessageDelivery) -> None:
        metadata_json = (
            json.dumps(delivery.metadata, ensure_ascii=False)
            if delivery.metadata
            else None
        )
        async with self._engine.write_lock:
            await self._engine.execute(
                """
                INSERT INTO message_deliveries (
                    delivery_id, target_chat_address, platform, target_type,
                    target_id, source_session_id, source_chat_address,
                    source_user_id, source, delivery_mode, status, message_id,
                    text, error, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    delivery.delivery_id,
                    delivery.target_chat_address,
                    delivery.platform,
                    delivery.target_type,
                    delivery.target_id,
                    delivery.source_session_id,
                    delivery.source_chat_address,
                    delivery.source_user_id,
                    delivery.source,
                    delivery.delivery_mode,
                    delivery.status,
                    delivery.message_id,
                    delivery.text,
                    delivery.error,
                    metadata_json,
                    delivery.created_at,
                ),
            )
            await self._engine.db.commit()

    async def list_groups(self, *, limit: int = 200) -> list[MessageDeliveryGroup]:
        """Return target chat groups ordered by most recent delivery."""
        rows = await self._engine.fetch_all(
            """
            WITH ranked AS (
                SELECT
                    target_chat_address,
                    platform,
                    target_type,
                    target_id,
                    source,
                    created_at,
                    COUNT(*) OVER (PARTITION BY target_chat_address) AS count,
                    ROW_NUMBER() OVER (
                        PARTITION BY target_chat_address
                        ORDER BY created_at DESC, delivery_id DESC
                    ) AS rn
                FROM message_deliveries
            )
            SELECT target_chat_address, platform, target_type, target_id,
                   source AS last_source, created_at AS last_created_at, count
            FROM ranked
            WHERE rn = 1
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [
            MessageDeliveryGroup(
                target_chat_address=str(row["target_chat_address"]),
                platform=str(row["platform"]),
                target_type=str(row["target_type"]),
                target_id=str(row["target_id"]),
                count=int(row["count"]),
                last_created_at=str(row["last_created_at"]),
                last_source=str(row["last_source"] or ""),
            )
            for row in rows
        ]

    async def list_for_target(
        self,
        target_chat_address: str,
        *,
        limit: int = 200,
    ) -> list[MessageDelivery]:
        rows = await self._engine.fetch_all(
            """
            SELECT delivery_id, target_chat_address, platform, target_type,
                   target_id, source_session_id, source_chat_address,
                   source_user_id, source, delivery_mode, status, message_id,
                   text, error, metadata_json, created_at
            FROM message_deliveries
            WHERE target_chat_address = ?
            ORDER BY created_at DESC, delivery_id DESC
            LIMIT ?
            """,
            (target_chat_address, limit),
        )
        return [self._row_to_delivery(row) for row in rows]

    async def search(
        self,
        query: str = "",
        *,
        target_chat_address: str = "",
        source: str = "",
        status: str = "",
        limit: int = 100,
    ) -> list[MessageDelivery]:
        where: list[str] = []
        params: list[Any] = []
        if query:
            pattern = _like_pattern(query)
            where.append(
                "("
                "text LIKE ? ESCAPE '\\' OR "
                "message_id LIKE ? ESCAPE '\\' OR "
                "error LIKE ? ESCAPE '\\' OR "
                "metadata_json LIKE ? ESCAPE '\\'"
                ")"
            )
            params.extend([pattern, pattern, pattern, pattern])
        if target_chat_address:
            where.append("target_chat_address = ?")
            params.append(target_chat_address)
        if source:
            where.append("source = ?")
            params.append(source)
        if status:
            where.append("status = ?")
            params.append(status)

        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        params.append(limit)
        rows = await self._engine.fetch_all(
            """
            SELECT delivery_id, target_chat_address, platform, target_type,
                   target_id, source_session_id, source_chat_address,
                   source_user_id, source, delivery_mode, status, message_id,
                   text, error, metadata_json, created_at
            FROM message_deliveries
            """
            + where_sql
            + """
            ORDER BY created_at DESC, delivery_id DESC
            LIMIT ?
            """,
            tuple(params),
        )
        return [self._row_to_delivery(row) for row in rows]

    @staticmethod
    def _row_to_delivery(row: aiosqlite.Row) -> MessageDelivery:
        metadata_raw = row["metadata_json"]
        metadata: dict[str, Any] = {}
        if isinstance(metadata_raw, str):
            try:
                parsed = json.loads(metadata_raw)
            except (json.JSONDecodeError, ValueError):
                parsed = {}
            if isinstance(parsed, dict):
                metadata = parsed
        return MessageDelivery(
            delivery_id=str(row["delivery_id"]),
            target_chat_address=str(row["target_chat_address"]),
            platform=str(row["platform"]),
            target_type=str(row["target_type"]),
            target_id=str(row["target_id"]),
            source_session_id=str(row["source_session_id"]),
            source_chat_address=str(row["source_chat_address"]),
            source_user_id=str(row["source_user_id"]),
            source=str(row["source"]),
            delivery_mode=str(row["delivery_mode"]),
            status=str(row["status"]),
            message_id=str(row["message_id"]),
            text=str(row["text"]),
            error=str(row["error"]),
            metadata=metadata,
            created_at=str(row["created_at"]),
        )


def _like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
