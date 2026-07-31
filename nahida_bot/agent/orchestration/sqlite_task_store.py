"""SQLite implementation of the orchestration task ledger."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from nahida_bot.agent.orchestration.models import (
    AgentRunStatus,
    BackgroundTask,
    TaskRuntime,
    utc_now,
)
from nahida_bot.agent.orchestration.task_store import BackgroundTaskStore
from nahida_bot.db.engine import DatabaseEngine


def _dt_to_str(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _dt_from_str(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _row_to_task(row: Any) -> BackgroundTask:
    delivery_raw = row["delivery_target_json"]
    delivery: dict[str, str] | None = None
    if isinstance(delivery_raw, str) and delivery_raw:
        try:
            parsed = json.loads(delivery_raw)
            if isinstance(parsed, dict):
                delivery = {str(k): str(v) for k, v in parsed.items()}
        except (json.JSONDecodeError, ValueError, TypeError):
            delivery = None

    return BackgroundTask(
        task_id=row["task_id"],
        runtime=TaskRuntime(row["runtime"]),
        status=AgentRunStatus(row["status"]),
        requester_session_id=row["requester_session_id"],
        child_session_id=row["child_session_id"],
        parent_task_id=row["parent_task_id"],
        title=row["title"],
        summary=row["summary"] or "",
        delivery_target=delivery,
        created_at=_dt_from_str(row["created_at"]) or utc_now(),
        updated_at=_dt_from_str(row["updated_at"]) or utc_now(),
        ended_at=_dt_from_str(row["ended_at"]),
        error=row["error"] or "",
        terminal_state=row["terminal_state"] or "",
        terminal_reason=row["terminal_reason"] or "",
        delivery_claimed_at=_dt_from_str(row["delivery_claimed_at"]),
        delivered_at=_dt_from_str(row["delivered_at"]),
    )


class SQLiteBackgroundTaskStore(BackgroundTaskStore):
    """Background task ledger backed by the shared SQLite engine."""

    def __init__(self, engine: DatabaseEngine) -> None:
        self._engine = engine

    async def create(self, task: BackgroundTask) -> None:
        delivery_json = (
            json.dumps(task.delivery_target, ensure_ascii=False)
            if task.delivery_target
            else None
        )
        async with self._engine.write_lock:
            await self._engine.execute(
                """
                INSERT INTO background_tasks (
                    task_id, runtime, status, requester_session_id,
                    child_session_id, parent_task_id, title, summary,
                    delivery_target_json, created_at, updated_at, ended_at, error,
                    terminal_state, terminal_reason, delivery_claimed_at,
                    delivered_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.task_id,
                    task.runtime.value,
                    task.status.value,
                    task.requester_session_id,
                    task.child_session_id,
                    task.parent_task_id,
                    task.title,
                    task.summary,
                    delivery_json,
                    _dt_to_str(task.created_at),
                    _dt_to_str(task.updated_at),
                    _dt_to_str(task.ended_at),
                    task.error,
                    task.terminal_state,
                    task.terminal_reason,
                    _dt_to_str(task.delivery_claimed_at),
                    _dt_to_str(task.delivered_at),
                ),
            )
            await self._engine.db.commit()

    async def get(self, task_id: str) -> BackgroundTask | None:
        row = await self._engine.fetch_one(
            "SELECT * FROM background_tasks WHERE task_id = ?",
            (task_id,),
        )
        return _row_to_task(row) if row is not None else None

    async def list_for_session(
        self, requester_session_id: str, *, limit: int = 20
    ) -> list[BackgroundTask]:
        rows = await self._engine.fetch_all(
            """
            SELECT * FROM background_tasks
            WHERE requester_session_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (requester_session_id, limit),
        )
        return [_row_to_task(row) for row in rows]

    async def update_status(
        self,
        task_id: str,
        status: AgentRunStatus,
        *,
        summary: str = "",
        error: str = "",
        terminal: bool = False,
        terminal_state: str = "",
        terminal_reason: str = "",
    ) -> None:
        now = utc_now()
        ended_at = now if terminal else None
        async with self._engine.write_lock:
            await self._engine.execute(
                """
                UPDATE background_tasks
                SET status = ?,
                    summary = CASE WHEN ? != '' THEN ? ELSE summary END,
                    error = CASE WHEN ? != '' THEN ? ELSE error END,
                    terminal_state = CASE WHEN ? != '' THEN ? ELSE terminal_state END,
                    terminal_reason = CASE WHEN ? != '' THEN ? ELSE terminal_reason END,
                    updated_at = ?,
                    ended_at = CASE WHEN ? IS NOT NULL THEN ? ELSE ended_at END
                WHERE task_id = ?
                """,
                (
                    status.value,
                    summary,
                    summary,
                    error,
                    error,
                    terminal_state,
                    terminal_state,
                    terminal_reason,
                    terminal_reason,
                    _dt_to_str(now),
                    _dt_to_str(ended_at),
                    _dt_to_str(ended_at),
                    task_id,
                ),
            )
            await self._engine.db.commit()

    async def claim_delivery(
        self,
        task_id: str,
        *,
        claimed_at: datetime,
        stale_before: datetime,
    ) -> bool:
        """Atomically acquire the right to attempt one completion delivery."""
        claimed = _dt_to_str(claimed_at)
        stale = _dt_to_str(stale_before)
        async with self._engine.write_lock:
            cursor = await self._engine.execute(
                """
                UPDATE background_tasks
                SET delivery_claimed_at = ?, updated_at = ?
                WHERE task_id = ?
                  AND delivered_at IS NULL
                  AND (
                      delivery_claimed_at IS NULL
                      OR delivery_claimed_at < ?
                  )
                """,
                (claimed, claimed, task_id, stale),
            )
            await self._engine.db.commit()
            return cursor.rowcount > 0

    async def release_delivery_claim(
        self, task_id: str, *, claimed_at: datetime
    ) -> bool:
        """Release only the exact claim held by this delivery attempt."""
        async with self._engine.write_lock:
            cursor = await self._engine.execute(
                """
                UPDATE background_tasks
                SET delivery_claimed_at = NULL, updated_at = ?
                WHERE task_id = ?
                  AND delivered_at IS NULL
                  AND delivery_claimed_at = ?
                """,
                (_dt_to_str(utc_now()), task_id, _dt_to_str(claimed_at)),
            )
            await self._engine.db.commit()
            return cursor.rowcount > 0

    async def mark_delivered(
        self,
        task_id: str,
        *,
        claimed_at: datetime,
        delivered_at: datetime,
    ) -> bool:
        """Confirm delivery only for the exact winning claim."""
        async with self._engine.write_lock:
            cursor = await self._engine.execute(
                """
                UPDATE background_tasks
                SET delivered_at = ?, delivery_claimed_at = NULL, updated_at = ?
                WHERE task_id = ?
                  AND delivered_at IS NULL
                  AND delivery_claimed_at = ?
                """,
                (
                    _dt_to_str(delivered_at),
                    _dt_to_str(delivered_at),
                    task_id,
                    _dt_to_str(claimed_at),
                ),
            )
            await self._engine.db.commit()
            return cursor.rowcount > 0
