"""Fairness, cancellation races and deadline inheritance for shared admission."""

import asyncio
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nahida_bot.core.exec_runner import ExecResult, ExecRunner
from nahida_bot.core.task_runtime import (
    AdmissionError,
    ExecutionAdmission,
    deadline_scope,
    remaining_seconds,
)


async def test_owner_round_robin_does_not_drain_hot_owner_first():
    pool = ExecutionAdmission(1, 32, 16)
    order = []

    async def work(owner, label):
        async with pool.slot(owner, "interactive", 1):
            order.append(label)

    async with pool.slot("blocker", "interactive", 1):
        tasks = [
            asyncio.create_task(work(owner, label))
            for owner, label in [("hot", "hot1"), ("hot", "hot2"), ("other", "other")]
        ]
        await asyncio.sleep(0)
    await asyncio.gather(*tasks)
    assert order == ["hot1", "other", "hot2"]
    assert pool.snapshot == {"capacity": 1, "running": 0, "queued": 0, "quarantined": 0}


async def test_background_and_maintenance_progress_under_interactive_load():
    pool = ExecutionAdmission(1, 64, 32)
    order = []

    async def work(service_class):
        async with pool.slot(service_class, service_class, 1):
            order.append(service_class)

    async with pool.slot("blocker", "interactive", 1):
        tasks = [
            asyncio.create_task(work(service_class))
            for service_class in ("interactive", "background", "maintenance")
            for _ in range(8)
        ]
        await asyncio.sleep(0)
    await asyncio.gather(*tasks)
    assert Counter(order[:7]) == {"interactive": 4, "background": 2, "maintenance": 1}


@pytest.mark.parametrize("when", ["queued", "granted"])
async def test_cancelled_ticket_never_leaks_a_slot(when):
    pool = ExecutionAdmission(1, 8, 4)
    executed = False

    async def work():
        nonlocal executed
        async with pool.slot("other", "background", 1):
            executed = True

    async with pool.slot("first", "interactive", 1):
        task = asyncio.create_task(work())
        await asyncio.sleep(0)
        if when == "queued":
            task.cancel()
    if when == "granted":
        # Grant occurred synchronously, but the waiter has not resumed yet.
        task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not executed
    async with pool.slot("next", "interactive", 1):
        assert pool.snapshot["running"] == 1
    assert pool.snapshot["queued"] == pool.snapshot["running"] == 0


async def test_queue_bounds_and_timeout_do_not_start_work():
    pool = ExecutionAdmission(1, 2, 1)

    async def wait(owner, timeout):
        async with pool.slot(owner, "background", timeout):
            pytest.fail("Queued work must not execute")

    async with pool.slot("running", "interactive", 1):
        first = asyncio.create_task(wait("one", 0.02))
        await asyncio.sleep(0)
        with pytest.raises(AdmissionError, match="queue is full"):
            await wait("one", 1)
        second = asyncio.create_task(wait("two", 1))
        await asyncio.sleep(0)
        with pytest.raises(AdmissionError, match="queue is full"):
            await wait("three", 1)
        with pytest.raises(AdmissionError) as expired:
            await first
        assert expired.value.code == "queue_timeout"
        second.cancel()
        await asyncio.gather(second, return_exceptions=True)
    assert pool.snapshot["queued"] == pool.snapshot["running"] == 0


def test_nested_deadline_cannot_extend_parent_and_resets():
    assert remaining_seconds() is None
    with deadline_scope(10):
        with deadline_scope(100):
            assert 0 < remaining_seconds() <= 10
        with deadline_scope(1):
            assert 0 < remaining_seconds() <= 1
    assert remaining_seconds() is None


async def test_managed_background_task_does_not_inherit_tool_deadline():
    from nahida_bot.core.tasks import TaskManager

    async def observe():
        return remaining_seconds()

    manager = TaskManager()
    try:
        with deadline_scope(0.01):
            task = manager.spawn("background", observe(), owner="test")
        assert await task is None
    finally:
        await manager.shutdown()


async def test_runner_queue_timeout_does_not_invoke_executor(monkeypatch):
    runner = ExecRunner(
        max_concurrency=1, queue_timeout_seconds=0.02, min_available_memory_mb=0
    )
    execute = AsyncMock(return_value=ExecResult(0))
    monkeypatch.setattr(runner, "_run", execute)
    async with runner.admission.slot("first", "interactive", 1):
        result = await runner.run("queued", timeout=5, cwd=None, env=None)
    assert result.termination_reason == "queue_timeout"
    assert result.started is False
    execute.assert_not_awaited()


async def test_parent_budget_reduces_execution_after_queueing(monkeypatch):
    runner = ExecRunner(min_available_memory_mb=0)
    execute = AsyncMock(return_value=ExecResult(0))
    monkeypatch.setattr(runner, "_run", execute)
    with deadline_scope(0.1):
        result = await runner.run("short parent", timeout=600, cwd=None, env=None)
    assert result.succeeded
    assert 0 < execute.call_args.kwargs["timeout"] < 0.1
    assert result.execution_timeout_seconds == execute.call_args.kwargs["timeout"]


async def test_memory_reservations_prevent_concurrent_over_admission(monkeypatch):
    runner = ExecRunner(
        max_concurrency=2, memory_limit_mb=100, min_available_memory_mb=50
    )
    monkeypatch.setattr(
        "psutil.virtual_memory", lambda: SimpleNamespace(available=200 * 1024**2)
    )
    entered, release = asyncio.Event(), asyncio.Event()

    async def execute(*args, **kwargs):
        entered.set()
        await release.wait()
        return ExecResult(0)

    monkeypatch.setattr(runner, "_run", execute)
    first = asyncio.create_task(runner.run("first", timeout=5, cwd=None, env=None))
    await entered.wait()
    try:
        result = await runner.run("second", timeout=5, cwd=None, env=None)
        assert result.termination_reason == "host_memory_pressure"
        assert result.started is False
    finally:
        release.set()
        await first
    assert (await runner.run("after release", timeout=5, cwd=None, env=None)).succeeded


async def test_uncertain_cleanup_does_not_reuse_execution_slot(monkeypatch):
    runner = ExecRunner(
        max_concurrency=1, min_available_memory_mb=0, queue_timeout_seconds=0.01
    )
    execute = AsyncMock(
        return_value=ExecResult(
            termination_reason="cleanup_failed", started=True, cleanup_succeeded=False
        )
    )
    monkeypatch.setattr(runner, "_run", execute)
    result = await runner.run("failed cleanup", timeout=5, cwd=None, env=None)
    assert result.termination_reason == "cleanup_failed"
    assert runner.admission.snapshot["quarantined"] == 1
    refused = await runner.run("must wait", timeout=5, cwd=None, env=None)
    assert refused.termination_reason == "queue_timeout"
    execute.assert_awaited_once()


async def test_oversized_payload_is_rejected_before_queueing(monkeypatch):
    runner = ExecRunner(min_available_memory_mb=0)
    execute = AsyncMock()
    monkeypatch.setattr(runner, "_run", execute)
    result = await runner.run("x" * (1024 * 1024), timeout=5, cwd=None, env=None)
    assert result.termination_reason == "request_too_large"
    assert result.started is False
    execute.assert_not_awaited()
    assert runner.admission.snapshot["queued"] == 0
