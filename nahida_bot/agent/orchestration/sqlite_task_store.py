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
                    delivery_target_json, created_at, updated_at, ended_at, error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    _dt_to_str(now),
                    _dt_to_str(ended_at),
                    _dt_to_str(ended_at),
                    task_id,
                ),
            )
            await self._engine.db.commit()
