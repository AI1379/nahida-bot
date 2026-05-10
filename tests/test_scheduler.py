"""Tests for cron scheduler persistence and execution semantics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from nahida_bot.agent.loop import AgentRunResult
from nahida_bot.core.session_runner import SessionRunner
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
        cron_expression=None,
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
        return AgentRunResult(final_response="done")


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


def _make_service(
    engine: DatabaseEngine,
    repo: CronRepository,
    *,
    agent: Any = None,
    channel: _Channel | None = None,
    config: SchedulerConfig | None = None,
) -> SchedulerService:
    runner = SessionRunner(agent_loop=agent)
    return SchedulerService(
        repo,
        runner=runner,
        channel_registry=cast(Any, _Channels(channel)) if channel else None,
        config=config,
    )


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
        service = _make_service(
            engine,
            repo,
            agent=_Agent(),
            channel=channel,
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
        service = _make_service(
            engine,
            repo,
            agent=_Agent(fail=True),
            channel=channel,
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
        assert stored.last_error is not None
        assert "RuntimeError" in stored.last_error
        assert "boom" in stored.last_error
        assert "[Scheduler] Scheduled task failed." == channel.sent[0][1].text
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_update_and_delete_job() -> None:
    engine, repo = await _repo()
    try:
        service = _make_service(engine, repo)
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


@pytest.mark.asyncio
async def test_stale_claims_recovered_on_start() -> None:
    engine, repo = await _repo()
    try:
        # Insert a job and simulate a stale claim
        await repo.insert_job(_job())
        await repo.claim_due_jobs(datetime.now(UTC).isoformat(), limit=1)

        # Verify the claim was set in the DB
        stored = await repo.get_job("job1")
        assert stored is not None
        assert stored.claimed_at is not None

        # Starting the service should release the stale claim
        service = _make_service(engine, repo)
        await service.start()
        await service.stop()

        stored = await repo.get_job("job1")
        assert stored is not None
        assert stored.claimed_at is None
        assert stored.is_active is True
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_cron_mode_creates_and_fires() -> None:
    engine, repo = await _repo()
    try:
        service = _make_service(engine, repo)
        # "every minute" expression — next fire is within a minute
        job = await service.create_job(
            platform="telegram",
            chat_id="c1",
            prompt="cron test",
            mode="cron",
            cron_expression="* * * * *",
            max_runs=2,
        )
        assert job.mode == "cron"
        assert job.cron_expression == "* * * * *"
        assert job.max_runs == 2
        assert job.is_active is True
        # next_fire_at should be in the future
        next_dt = datetime.fromisoformat(job.next_fire_at)
        assert next_dt > datetime.now(UTC) - timedelta(seconds=1)

        # Simulate a fire: claim, fire, complete — should schedule next
        claimed = await repo.claim_due_jobs(
            (datetime.now(UTC) + timedelta(minutes=1)).isoformat(), limit=1
        )
        assert len(claimed) == 1
        channel = _Channel()
        svc = _make_service(
            engine,
            repo,
            agent=_Agent(),
            channel=channel,
            config=SchedulerConfig(job_timeout_seconds=5),
        )
        await svc._fire_job(claimed[0])
        stored = await repo.get_job(job.job_id)
        assert stored is not None
        assert stored.run_count == 1
        assert stored.is_active is True  # still active (max_runs=2, only 1 done)
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_cron_mode_update_and_invalid_expression() -> None:
    engine, repo = await _repo()
    try:
        service = _make_service(engine, repo)
        job = await service.create_job(
            platform="telegram",
            chat_id="c1",
            prompt="cron test",
            mode="cron",
            cron_expression="0 9 * * *",
        )

        # Update to a different cron expression
        updated = await service.update_job(
            job.job_id,
            cron_expression="0 12 * * 1-5",
        )
        assert updated.cron_expression == "0 12 * * 1-5"
        assert updated.mode == "cron"

        # Invalid cron expression should raise
        with pytest.raises(ValueError, match="Invalid cron expression"):
            await service.create_job(
                platform="telegram",
                chat_id="c1",
                prompt="bad cron",
                mode="cron",
                cron_expression="not-a-cron",
            )

        # Missing cron_expression should raise
        with pytest.raises(ValueError, match="cron_expression is required"):
            await service.create_job(
                platform="telegram",
                chat_id="c1",
                prompt="no expr",
                mode="cron",
            )

        with pytest.raises(ValueError, match="standard 5-field syntax"):
            await service.create_job(
                platform="telegram",
                chat_id="c1",
                prompt="six fields",
                mode="cron",
                cron_expression="* * * * * *",
            )
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_cron_mode_respects_min_interval_config() -> None:
    engine, repo = await _repo()
    try:
        service = _make_service(
            engine,
            repo,
            config=SchedulerConfig(min_interval_seconds=120),
        )

        with pytest.raises(ValueError, match="interval must be >= 120 seconds"):
            await service.create_job(
                platform="telegram",
                chat_id="c1",
                prompt="too often",
                mode="cron",
                cron_expression="* * * * *",
            )
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_quota_enforced_atomically() -> None:
    engine, repo = await _repo()
    try:
        config = SchedulerConfig(max_jobs_per_chat=2)
        service = _make_service(engine, repo, config=config)

        fire_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        await service.create_job(
            platform="telegram", chat_id="c1", prompt="j1", mode="once", fire_at=fire_at
        )
        await service.create_job(
            platform="telegram", chat_id="c1", prompt="j2", mode="once", fire_at=fire_at
        )

        with pytest.raises(ValueError, match="limit reached"):
            await service.create_job(
                platform="telegram",
                chat_id="c1",
                prompt="j3",
                mode="once",
                fire_at=fire_at,
            )
    finally:
        await engine.close()
