"""Tests for cron scheduler persistence and execution semantics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest

from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.plugins.base import OutboundMessage
from nahida_bot.scheduler.models import CronJob, SchedulerConfig
from nahida_bot.scheduler.repository import CronRepository
from nahida_bot.scheduler.service import SchedulerService


async def _repo() -> tuple[DatabaseEngine, CronRepository]:
    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    return engine, CronRepository(engine)


def _job(*, job_id: str = "job1", next_fire_at: str | None = None) -> CronJob:
    now = datetime.now(UTC).isoformat()
    return CronJob(
        job_id=job_id,
        platform="telegram",
        chat_id="c1",
        session_key="telegram:c1",
        prompt="say hi",
        mode="once",
        fire_at=next_fire_at or now,
        interval_seconds=None,
        max_runs=None,
        run_count=0,
        is_active=True,
        created_at=now,
        next_fire_at=next_fire_at or now,
        last_fired_at=None,
        workspace_id=None,
    )


class _Agent:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def run(self, **kwargs: object) -> object:
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        return SimpleNamespace(final_response="done")


class _Channel:
    def __init__(self) -> None:
        self.sent: list[tuple[str, OutboundMessage]] = []

    async def send_message(self, chat_id: str, message: OutboundMessage) -> str:
        self.sent.append((chat_id, message))
        return "m1"


class _Channels:
    def __init__(self, channel: _Channel) -> None:
        self.channel = channel

    def get(self, platform: str) -> _Channel | None:
        if platform == "telegram":
            return self.channel
        return None


@pytest.mark.asyncio
async def test_claim_due_jobs_is_atomic_and_hides_claimed_jobs() -> None:
    engine, repo = await _repo()
    try:
        due_at = datetime.now(UTC).isoformat()
        await repo.insert_job(_job(next_fire_at=due_at))

        first = await repo.claim_due_jobs(datetime.now(UTC).isoformat(), limit=10)
        second = await repo.claim_due_jobs(datetime.now(UTC).isoformat(), limit=10)

        assert [j.job_id for j in first] == ["job1"]
        assert second == []
        stored = await repo.get_job("job1")
        assert stored is not None
        assert stored.claimed_at is not None
        assert stored.run_count == 0
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_fire_job_completes_once_job_after_success() -> None:
    engine, repo = await _repo()
    try:
        await repo.insert_job(_job())
        claimed = await repo.claim_due_jobs(datetime.now(UTC).isoformat(), limit=1)
        channel = _Channel()
        service = SchedulerService(
            repo,
            agent_loop=cast(Any, _Agent()),
            channel_registry=cast(Any, _Channels(channel)),
            config=SchedulerConfig(job_timeout_seconds=1),
        )

        await service._fire_job(claimed[0])

        stored = await repo.get_job("job1")
        assert stored is not None
        assert stored.is_active is False
        assert stored.claimed_at is None
        assert stored.run_count == 1
        assert channel.sent[0][1].text == "done"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_fire_job_failure_releases_claim_for_retry_without_counting_run() -> None:
    engine, repo = await _repo()
    try:
        await repo.insert_job(_job())
        claimed = await repo.claim_due_jobs(datetime.now(UTC).isoformat(), limit=1)
        channel = _Channel()
        service = SchedulerService(
            repo,
            agent_loop=cast(Any, _Agent(fail=True)),
            channel_registry=cast(Any, _Channels(channel)),
            config=SchedulerConfig(
                job_timeout_seconds=1,
                failure_retry_seconds=60,
                max_consecutive_failures=3,
            ),
        )

        await service._fire_job(claimed[0])

        stored = await repo.get_job("job1")
        assert stored is not None
        assert stored.is_active is True
        assert stored.claimed_at is None
        assert stored.run_count == 0
        assert stored.failure_count == 1
        assert stored.last_error == "RuntimeError"
        assert "[Scheduler] Scheduled task failed." == channel.sent[0][1].text
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_update_and_delete_job() -> None:
    engine, repo = await _repo()
    try:
        service = SchedulerService(repo)
        fire_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        job = await service.create_job(
            platform="telegram",
            chat_id="c1",
            prompt="old",
            mode="once",
            fire_at=fire_at,
        )

        updated = await service.update_job(
            job.job_id,
            prompt="new",
            mode="interval",
            interval_seconds=120,
            max_runs=2,
        )
        assert updated.prompt == "new"
        assert updated.mode == "interval"
        assert updated.interval_seconds == 120
        assert updated.max_runs == 2

        assert await service.delete_job(job.job_id) is True
        assert await service.get_job(job.job_id) is None
    finally:
        await engine.close()
