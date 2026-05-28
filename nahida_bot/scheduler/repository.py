"""SQLite data access for cron_jobs table."""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiosqlite
import structlog

from nahida_bot.scheduler.models import CronJob

if TYPE_CHECKING:
    from nahida_bot.db.engine import DatabaseEngine

_logger = structlog.get_logger(__name__)


def _row_to_job(r: aiosqlite.Row) -> CronJob:
    """Convert a database row to a CronJob dataclass."""
    keys = r.keys()
    return CronJob(
        job_id=r["job_id"],
        platform=r["platform"],
        chat_id=r["chat_id"],
        session_key=r["session_key"],
        prompt=r["prompt"],
        mode=r["mode"],
        fire_at=r["fire_at"],
        interval_seconds=r["interval_seconds"],
        cron_expression=r["cron_expression"] if "cron_expression" in keys else None,
        max_runs=r["max_runs"],
        run_count=r["run_count"],
        is_active=bool(r["is_active"]),
        created_at=r["created_at"],
        next_fire_at=r["next_fire_at"],
        last_fired_at=r["last_fired_at"],
        workspace_id=r["workspace_id"],
        claimed_at=r["claimed_at"],
        failure_count=r["failure_count"],
        last_error=r["last_error"],
        session_mode=r["session_mode"] if "session_mode" in keys else "main",
        session_name=r["session_name"] if "session_name" in keys else None,
        chat_type=r["chat_type"] if "chat_type" in keys else "",
        created_by_user_id=(
            r["created_by_user_id"] if "created_by_user_id" in keys else ""
        ),
        created_from_session_id=(
            r["created_from_session_id"] if "created_from_session_id" in keys else ""
        ),
        created_from_chat_address=(
            r["created_from_chat_address"]
            if "created_from_chat_address" in keys
            else ""
        ),
    )


class CronRepository:
    """SQLite data access for scheduled cron jobs."""

    def __init__(self, engine: DatabaseEngine) -> None:
        self._engine = engine

    async def insert_job(self, job: CronJob) -> None:
        async with self._engine.write_lock:
            await self._engine.execute(
                """
                INSERT INTO cron_jobs (
                    job_id, platform, chat_id, session_key, prompt, mode,
                    fire_at, interval_seconds, cron_expression, max_runs,
                    run_count, is_active, created_at, next_fire_at,
                    last_fired_at, workspace_id, claimed_at, failure_count,
                    last_error, session_mode, session_name, chat_type,
                    created_by_user_id, created_from_session_id,
                    created_from_chat_address
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.platform,
                    job.chat_id,
                    job.session_key,
                    job.prompt,
                    job.mode,
                    job.fire_at,
                    job.interval_seconds,
                    job.cron_expression,
                    job.max_runs,
                    job.run_count,
                    int(job.is_active),
                    job.created_at,
                    job.next_fire_at,
                    job.last_fired_at,
                    job.workspace_id,
                    job.claimed_at,
                    job.failure_count,
                    job.last_error,
                    job.session_mode,
                    job.session_name,
                    job.chat_type,
                    job.created_by_user_id,
                    job.created_from_session_id,
                    job.created_from_chat_address,
                ),
            )
            await self._engine.db.commit()

    async def claim_due_jobs(self, now_iso: str, *, limit: int) -> list[CronJob]:
        """Atomically claim due jobs for execution.

        A claimed job is hidden from subsequent polls until it is completed or
        marked failed, which prevents duplicate fires in this process and across
        multiple processes sharing the same SQLite database.
        """
        if limit <= 0:
            return []

        claimed: list[CronJob] = []
        async with self._engine.write_lock:
            rows = await self._engine.fetch_all(
                """
                SELECT * FROM cron_jobs
                WHERE is_active = 1
                  AND claimed_at IS NULL
                  AND next_fire_at <= ?
                ORDER BY next_fire_at
                LIMIT ?
                """,
                (now_iso, limit),
            )
            for row in rows:
                cursor = await self._engine.execute(
                    """
                    UPDATE cron_jobs
                    SET claimed_at = ?
                    WHERE job_id = ?
                      AND is_active = 1
                      AND claimed_at IS NULL
                      AND next_fire_at = ?
                    """,
                    (now_iso, row["job_id"], row["next_fire_at"]),
                )
                if cursor.rowcount > 0:
                    claimed.append(_row_to_job(row))
            await self._engine.db.commit()
        return claimed

    async def get_jobs_by_chat(
        self,
        session_key: str,
        *,
        active_only: bool = True,
    ) -> list[CronJob]:
        if active_only:
            rows = await self._engine.fetch_all(
                """
                SELECT * FROM cron_jobs
                WHERE session_key = ? AND is_active = 1
                ORDER BY next_fire_at
                """,
                (session_key,),
            )
        else:
            rows = await self._engine.fetch_all(
                """
                SELECT * FROM cron_jobs
                WHERE session_key = ?
                ORDER BY next_fire_at
                """,
                (session_key,),
            )
        return [_row_to_job(r) for r in rows]

    async def get_job(self, job_id: str) -> CronJob | None:
        row = await self._engine.fetch_one(
            "SELECT * FROM cron_jobs WHERE job_id = ?",
            (job_id,),
        )
        if row is None:
            return None
        return _row_to_job(row)

    async def complete_fire(
        self,
        job_id: str,
        *,
        next_fire_at: str | None,
        fired_at: str,
    ) -> None:
        """Mark a claimed job as successfully fired.

        If next_fire_at is None, marks the job as inactive (completed).
        """
        async with self._engine.write_lock:
            if next_fire_at is not None:
                await self._engine.execute(
                    """
                    UPDATE cron_jobs
                    SET run_count = run_count + 1,
                        last_fired_at = ?,
                        next_fire_at = ?,
                        claimed_at = NULL,
                        failure_count = 0,
                        last_error = NULL
                    WHERE job_id = ?
                    """,
                    (fired_at, next_fire_at, job_id),
                )
            else:
                await self._engine.execute(
                    """
                    UPDATE cron_jobs
                    SET run_count = run_count + 1,
                        last_fired_at = ?,
                        next_fire_at = ?,
                        claimed_at = NULL,
                        failure_count = 0,
                        last_error = NULL,
                        is_active = 0
                    WHERE job_id = ?
                    """,
                    (fired_at, fired_at, job_id),
                )
            await self._engine.db.commit()

    async def mark_failed(
        self,
        job_id: str,
        *,
        retry_at: str,
        error: str,
        deactivate: bool,
    ) -> None:
        """Release a claimed job after a failed fire attempt."""
        async with self._engine.write_lock:
            if deactivate:
                await self._engine.execute(
                    """
                    UPDATE cron_jobs
                    SET claimed_at = NULL,
                        failure_count = failure_count + 1,
                        last_error = ?,
                        is_active = 0,
                        next_fire_at = ?
                    WHERE job_id = ?
                    """,
                    (error, retry_at, job_id),
                )
            else:
                await self._engine.execute(
                    """
                    UPDATE cron_jobs
                    SET claimed_at = NULL,
                        failure_count = failure_count + 1,
                        last_error = ?,
                        next_fire_at = ?
                    WHERE job_id = ?
                    """,
                    (error, retry_at, job_id),
                )
            await self._engine.db.commit()

    async def cancel_job(self, job_id: str) -> bool:
        async with self._engine.write_lock:
            cursor = await self._engine.execute(
                """
                UPDATE cron_jobs SET is_active = 0, claimed_at = NULL
                WHERE job_id = ? AND is_active = 1
                """,
                (job_id,),
            )
            await self._engine.db.commit()
            return cursor.rowcount > 0

    async def update_job(
        self,
        job_id: str,
        *,
        prompt: str,
        mode: str,
        fire_at: str | None,
        interval_seconds: int | None,
        cron_expression: str | None,
        max_runs: int | None,
        next_fire_at: str,
    ) -> bool:
        """Update an active, unclaimed job. Returns False if it cannot update."""
        async with self._engine.write_lock:
            cursor = await self._engine.execute(
                """
                UPDATE cron_jobs
                SET prompt = ?,
                    mode = ?,
                    fire_at = ?,
                    interval_seconds = ?,
                    cron_expression = ?,
                    max_runs = ?,
                    next_fire_at = ?,
                    failure_count = 0,
                    last_error = NULL
                WHERE job_id = ?
                  AND is_active = 1
                  AND claimed_at IS NULL
                """,
                (
                    prompt,
                    mode,
                    fire_at,
                    interval_seconds,
                    cron_expression,
                    max_runs,
                    next_fire_at,
                    job_id,
                ),
            )
            await self._engine.db.commit()
            return cursor.rowcount > 0

    async def delete_job(self, job_id: str) -> bool:
        async with self._engine.write_lock:
            cursor = await self._engine.execute(
                "DELETE FROM cron_jobs WHERE job_id = ?",
                (job_id,),
            )
            await self._engine.db.commit()
            return cursor.rowcount > 0

    async def count_active_jobs_by_chat(self, session_key: str) -> int:
        row = await self._engine.fetch_one(
            """
            SELECT COUNT(*) AS count
            FROM cron_jobs
            WHERE session_key = ? AND is_active = 1
            """,
            (session_key,),
        )
        return int(row["count"]) if row is not None else 0

    async def release_stale_claims(self) -> int:
        """Reset claimed_at for all jobs (recover from crash)."""
        async with self._engine.write_lock:
            cursor = await self._engine.execute(
                "UPDATE cron_jobs SET claimed_at = NULL WHERE claimed_at IS NOT NULL"
            )
            await self._engine.db.commit()
            return cursor.rowcount

    async def insert_job_with_quota(
        self,
        job: CronJob,
        *,
        max_per_chat: int,
    ) -> None:
        """Atomic count-check-then-insert. Raises ValueError if quota exceeded."""
        async with self._engine.write_lock:
            row = await self._engine.fetch_one(
                """
                SELECT COUNT(*) AS count
                FROM cron_jobs
                WHERE session_key = ? AND is_active = 1
                """,
                (job.session_key,),
            )
            count = int(row["count"]) if row is not None else 0
            if count >= max_per_chat:
                raise ValueError(
                    f"active scheduled task limit reached for this chat "
                    f"({max_per_chat})"
                )
            await self._engine.execute(
                """
                INSERT INTO cron_jobs (
                    job_id, platform, chat_id, session_key, prompt, mode,
                    fire_at, interval_seconds, cron_expression, max_runs,
                    run_count, is_active, created_at, next_fire_at,
                    last_fired_at, workspace_id, claimed_at, failure_count,
                    last_error, session_mode, session_name, chat_type,
                    created_by_user_id, created_from_session_id,
                    created_from_chat_address
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.platform,
                    job.chat_id,
                    job.session_key,
                    job.prompt,
                    job.mode,
                    job.fire_at,
                    job.interval_seconds,
                    job.cron_expression,
                    job.max_runs,
                    job.run_count,
                    int(job.is_active),
                    job.created_at,
                    job.next_fire_at,
                    job.last_fired_at,
                    job.workspace_id,
                    job.claimed_at,
                    job.failure_count,
                    job.last_error,
                    job.session_mode,
                    job.session_name,
                    job.chat_type,
                    job.created_by_user_id,
                    job.created_from_session_id,
                    job.created_from_chat_address,
                ),
            )
            await self._engine.db.commit()

    async def get_all_active_jobs(self) -> list[CronJob]:
        rows = await self._engine.fetch_all(
            "SELECT * FROM cron_jobs WHERE is_active = 1 ORDER BY next_fire_at"
        )
        return [_row_to_job(r) for r in rows]

    async def list_all_jobs(
        self,
        *,
        active: str = "all",
        limit: int = 100,
    ) -> list[CronJob]:
        """List all jobs, optionally filtered by active status.

        Args:
            active: "true" = active only, "false" = inactive only, "all" = everything.
            limit: Maximum rows.
        """
        if active == "true":
            rows = await self._engine.fetch_all(
                "SELECT * FROM cron_jobs WHERE is_active = 1 ORDER BY next_fire_at LIMIT ?",
                (limit,),
            )
        elif active == "false":
            rows = await self._engine.fetch_all(
                "SELECT * FROM cron_jobs WHERE is_active = 0 ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        else:
            rows = await self._engine.fetch_all(
                "SELECT * FROM cron_jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        return [_row_to_job(r) for r in rows]
