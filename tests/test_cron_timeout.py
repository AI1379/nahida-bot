"""Per-job deadlines survive storage and override, or inherit, global limits."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from nahida_bot.scheduler.models import SchedulerConfig
from nahida_bot.scheduler.service import SchedulerService
from tests.test_scheduler import _address, _job, _repo


@pytest.mark.parametrize(
    "reason", ["busy", "queue_full", "queue_timeout", "host_memory_pressure"]
)
async def test_admission_deferral_keeps_occurrence_without_agent_or_failure(reason):
    from nahida_bot.core.exec_runner import ExecResult, ExecRunner

    engine, repo = await _repo()
    try:
        original = replace(
            _job(),
            executor_type="script_then_agent",
            script_command="not started",
            failure_count=2,
        )
        await repo.insert_job(original)
        claimed = (await repo.claim_due_jobs(datetime.now(UTC).isoformat(), limit=1))[0]
        runner = ExecRunner()
        runner.run = AsyncMock(
            return_value=ExecResult(
                termination_reason=reason,
                started=False,
            )
        )
        service = SchedulerService(repo, exec_runner=runner)
        service._execute_agent_fire = AsyncMock()
        await service._fire_job(claimed)
        stored = await repo.get_job(original.job_id)
        assert stored.claimed_at is None
        assert stored.is_active and stored.failure_count == 2 and stored.run_count == 0
        assert stored.next_fire_at == original.next_fire_at
        assert stored.fire_at == original.fire_at
        service._execute_agent_fire.assert_not_awaited()
        assert original.job_id in service._admission_retries
        # Backing off the oldest occurrence must not block another due job.
        await repo.insert_job(_job(job_id="other"))
        due = await repo.claim_due_jobs(
            datetime.now(UTC).isoformat(),
            limit=1,
            exclude_job_ids=tuple(service._admission_retries),
        )
        assert [job.job_id for job in due] == ["other"]
    finally:
        await engine.close()


async def test_running_memory_pressure_is_not_mistaken_for_admission():
    from nahida_bot.core.exec_runner import ExecResult, ExecRunner

    engine, repo = await _repo()
    try:
        job = replace(_job(), executor_type="script_then_agent", script_command="ran")
        await repo.insert_job(job)
        runner = ExecRunner()
        runner.run = AsyncMock(
            return_value=ExecResult(
                1,
                termination_reason="host_memory_pressure",
                started=True,
            )
        )
        service = SchedulerService(repo, exec_runner=runner)
        service._execute_agent_fire = AsyncMock()
        await service._fire_job(job)
        service._execute_agent_fire.assert_awaited_once()
    finally:
        await engine.close()


async def test_shutdown_cancellation_releases_claim_without_failure_count(monkeypatch):
    engine, repo = await _repo()
    entered = asyncio.Event()
    try:
        await repo.insert_job(replace(_job(), failure_count=2))
        claimed = (await repo.claim_due_jobs(datetime.now(UTC).isoformat(), limit=1))[0]
        service = SchedulerService(repo)

        async def execute(_job):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(service, "_execute_fire", execute)
        task = asyncio.create_task(service._fire_job(claimed))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        stored = await repo.get_job(claimed.job_id)
        assert stored.claimed_at is None
        assert stored.is_active and stored.failure_count == 2
    finally:
        await engine.close()


async def test_timeout_create_update_clear_and_restart(tmp_path):
    from nahida_bot.db.engine import DatabaseEngine
    from nahida_bot.scheduler.repository import CronRepository

    engine = DatabaseEngine(tmp_path / "cron.db")
    await engine.initialize()
    try:
        service = SchedulerService(CronRepository(engine))
        job = await service.create_job(
            address=_address(),
            prompt="research",
            mode="interval",
            interval_seconds=3600,
            timeout_seconds=1800,
        )
        await engine.close()
        await engine.initialize()
        stored = await service.get_job(job.job_id)
        assert stored.timeout_seconds == 1800
        # A normal edit preserves the limit; explicit null restores inheritance.
        edited = await service.update_job(job.job_id, prompt="updated research")
        assert edited.timeout_seconds == 1800
        edited = await service.update_job(job.job_id, timeout_seconds=2400)
        assert edited.timeout_seconds == 2400
        edited = await service.update_job(job.job_id, timeout_seconds=None)
        assert edited.timeout_seconds is None
    finally:
        await engine.close()


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "600", float("inf")])
async def test_invalid_timeout_never_changes_saved_job(value):
    engine, repo = await _repo()
    try:
        service = SchedulerService(repo)
        with pytest.raises(ValueError, match="timeout_seconds"):
            await service.create_job(
                address=_address(),
                prompt="test",
                mode="interval",
                interval_seconds=60,
                timeout_seconds=value,
            )
        await repo.insert_job(replace(_job(), timeout_seconds=600))
        with pytest.raises(ValueError, match="timeout_seconds"):
            await service.update_job("job1", timeout_seconds=value)
        assert (await repo.get_job("job1")).timeout_seconds == 600
    finally:
        await engine.close()


@pytest.mark.parametrize(
    "override,default,delay,succeeds",
    [(None, 0.01, 0.05, False), (1, 0.01, 0.05, True), (1, 5, 1.1, False)],
)
async def test_effective_deadline_controls_execution(
    override, default, delay, succeeds, monkeypatch
):
    engine, repo = await _repo()
    try:
        await repo.insert_job(replace(_job(), timeout_seconds=override))
        job = (await repo.claim_due_jobs(datetime.now(UTC).isoformat(), limit=1))[0]
        service = SchedulerService(
            repo, config=SchedulerConfig(job_timeout_seconds=default)
        )
        cancelled = asyncio.Event()

        async def execute(_job):
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        monkeypatch.setattr(service, "_execute_fire", execute)
        notify = AsyncMock()
        monkeypatch.setattr(service, "_send_error", notify)
        await service._fire_job(job)
        stored = await repo.get_job("job1")
        assert stored.claimed_at is None
        assert stored.run_count == int(succeeds)
        assert stored.failure_count == int(not succeeds)
        assert cancelled.is_set() is not succeeds
        if not succeeds:
            notify.assert_awaited_once_with(
                job, "Timed out", timeout=override or default
            )
    finally:
        await engine.close()


async def test_existing_jobs_inherit_after_idempotent_migration(tmp_path):
    from nahida_bot.db.engine import DatabaseEngine
    from nahida_bot.scheduler.repository import CronRepository

    engine = DatabaseEngine(tmp_path / "legacy.db")
    await engine.initialize()
    try:
        await CronRepository(engine).insert_job(_job())
        await engine.execute("ALTER TABLE cron_jobs DROP COLUMN timeout_seconds")
        await engine.execute("UPDATE schema_version SET version=29")
        await engine.db.commit()
        for _ in range(2):
            await engine.close()
            await engine.initialize()
            job = await CronRepository(engine).get_job("job1")
            assert job.prompt == "say hi"
            assert job.timeout_seconds is None
    finally:
        await engine.close()
