"""SQLite persistence for Gateway-Node token records."""

from __future__ import annotations

import json

import aiosqlite

from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.gateway.services.node_auth import NodeTokenRecord


class SQLiteNodeTokenStore:
    """Persistence-backed node token store using the application database."""

    def __init__(self, engine: DatabaseEngine) -> None:
        self._engine = engine

    async def put(self, token_id: str, record: NodeTokenRecord) -> None:
        async with self._engine.write_lock:
            await self._engine.execute(
                """
                INSERT INTO node_tokens (
                    token_id, node_id, token_digest, token_type, created_at,
                    expires_at, revoked, used, display_name, scope_json,
                    actor_account_key, conversation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(token_id) DO UPDATE SET
                    node_id = excluded.node_id,
                    token_digest = excluded.token_digest,
                    token_type = excluded.token_type,
                    created_at = excluded.created_at,
                    expires_at = excluded.expires_at,
                    revoked = excluded.revoked,
                    used = excluded.used,
                    display_name = excluded.display_name,
                    scope_json = excluded.scope_json,
                    actor_account_key = excluded.actor_account_key,
                    conversation_id = excluded.conversation_id
                """,
                (
                    token_id,
                    record.node_id,
                    record.token_digest,
                    record.token_type,
                    record.created_at,
                    record.expires_at,
                    int(record.revoked),
                    int(record.used),
                    record.display_name,
                    json.dumps(record.scope),
                    record.actor_account_key,
                    record.conversation_id,
                ),
            )
            await self._engine.db.commit()

    async def get(self, token_id: str) -> NodeTokenRecord | None:
        row = await self._engine.fetch_one(
            "SELECT * FROM node_tokens WHERE token_id = ?", (token_id,)
        )
        return self._row_to_record(row) if row is not None else None

    async def delete(self, token_id: str) -> bool:
        async with self._engine.write_lock:
            cursor = await self._engine.execute(
                "DELETE FROM node_tokens WHERE token_id = ?", (token_id,)
            )
            await self._engine.db.commit()
            return cursor.rowcount > 0

    async def list_by_node(self, node_id: str) -> list[NodeTokenRecord]:
        rows = await self._engine.fetch_all(
            "SELECT * FROM node_tokens WHERE node_id = ? ORDER BY created_at",
            (node_id,),
        )
        return [self._row_to_record(row) for row in rows]

    async def mark_used(self, token_id: str) -> bool:
        async with self._engine.write_lock:
            cursor = await self._engine.execute(
                "UPDATE node_tokens SET used = 1 "
                "WHERE token_id = ? AND used = 0 AND revoked = 0",
                (token_id,),
            )
            await self._engine.db.commit()
            return cursor.rowcount > 0

    async def revoke(self, token_id: str) -> bool:
        async with self._engine.write_lock:
            cursor = await self._engine.execute(
                "UPDATE node_tokens SET revoked = 1 WHERE token_id = ?",
                (token_id,),
            )
            await self._engine.db.commit()
            return cursor.rowcount > 0

    async def revoke_all_for_node(self, node_id: str) -> int:
        async with self._engine.write_lock:
            cursor = await self._engine.execute(
                "UPDATE node_tokens SET revoked = 1 WHERE node_id = ? AND revoked = 0",
                (node_id,),
            )
            await self._engine.db.commit()
            return cursor.rowcount

    @staticmethod
    def _row_to_record(row: aiosqlite.Row) -> NodeTokenRecord:
        raw_scope = json.loads(str(row["scope_json"]))
        scope = (
            tuple(str(item) for item in raw_scope)
            if isinstance(raw_scope, list)
            else ()
        )
        return NodeTokenRecord(
            token_id=str(row["token_id"]),
            node_id=str(row["node_id"]),
            token_digest=str(row["token_digest"]),
            token_type=str(row["token_type"]),
            created_at=float(row["created_at"]),
            expires_at=float(row["expires_at"]),
            revoked=bool(row["revoked"]),
            used=bool(row["used"]),
            display_name=str(row["display_name"]),
            scope=scope,
            actor_account_key=str(row["actor_account_key"]),
            conversation_id=str(row["conversation_id"]),
        )


__all__ = ["SQLiteNodeTokenStore"]
