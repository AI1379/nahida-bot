"""Tests for the centralized TaskManager."""

from __future__ import annotations

import asyncio

import pytest

from nahida_bot.core.tasks import TaskManager


# ── Helpers ────────────────────────────────────────────────


async def _noop() -> None:
    pass


async def _hang_forever() -> None:
    """Block until cancelled."""
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        raise


async def _fail_once() -> None:
    """Raise an exception immediately."""
    raise RuntimeError("boom")


async def _count_calls(counter: list[int]) -> None:
    """Increment a simple list-based counter."""
    counter[0] += 1


# ── Spawn + List ───────────────────────────────────────────


class TestSpawn:
    async def test_spawn_creates_running_task(self) -> None:
        tm = TaskManager()
        task = tm.spawn("t1", _hang_forever(), owner="test")
        assert not task.done()

        info = tm.get_task("test:t1")
        assert info is not None
        assert info.name == "t1"
        assert info.owner == "test"
        assert info.status == "running"
        assert info.kind == "oneshot"

        await tm.shutdown()

    async def test_spawn_duplicate_name_raises(self) -> None:
        tm = TaskManager()
        tm.spawn("t1", _hang_forever(), owner="test")
        rejected = _hang_forever()
        with pytest.raises(ValueError, match="already exists"):
            tm.spawn("t1", rejected, owner="test")
        assert rejected.cr_frame is None
        await tm.shutdown()

    async def test_same_name_different_owners(self) -> None:
        tm = TaskManager()
        t1 = tm.spawn("poll", _hang_forever(), owner="plugin.a")
        t2 = tm.spawn("poll", _hang_forever(), owner="plugin.b")
        assert not t1.done()
        assert not t2.done()

        tasks = tm.list_tasks()
        assert len(tasks) == 2
        assert {t.owner for t in tasks} == {"plugin.a", "plugin.b"}
        await tm.shutdown()

    async def test_spawn_task_completes_normally(self) -> None:
        tm = TaskManager()
        tm.spawn("quick", _noop(), owner="test")
        # Give the event loop a turn
        await asyncio.sleep(0.05)

        info = tm.get_task("test:quick")
        assert info is not None
        assert info.status == "done"

    async def test_spawn_can_reuse_name_after_completion(self) -> None:
        tm = TaskManager()
        tm.spawn("quick", _noop(), owner="test")
        await asyncio.sleep(0.05)
        assert tm.get_task("test:quick").status == "done"  # type: ignore[union-attr]

        tm.spawn("quick", _noop(), owner="test")
        await asyncio.sleep(0.05)

        info = tm.get_task("test:quick")
        assert info is not None
        assert info.status == "done"

    async def test_spawn_task_fails(self) -> None:
        tm = TaskManager()
        tm.spawn("failing", _fail_once(), owner="test")
        await asyncio.sleep(0.05)

        info = tm.get_task("test:failing")
        assert info is not None
        assert info.status == "failed"
        assert "boom" in (info.error or "")


# ── Cancel ─────────────────────────────────────────────────


class TestCancel:
    async def test_cancel_by_key(self) -> None:
        tm = TaskManager()
        tm.spawn("t1", _hang_forever(), owner="test")
        assert tm.cancel("test:t1") is True
        await asyncio.sleep(0.05)

        info = tm.get_task("test:t1")
        assert info is not None
        assert info.status == "cancelled"

    async def test_cancel_nonexistent(self) -> None:
        tm = TaskManager()
        assert tm.cancel("nope:nope") is False

    async def test_cancel_and_await(self) -> None:
        tm = TaskManager()
        tm.spawn("t1", _hang_forever(), owner="test")
        result = await tm.cancel_and_await("test:t1", timeout=2.0)
        assert result is True
        assert tm.get_task("test:t1") is not None
        assert tm.get_task("test:t1").status == "cancelled"  # type: ignore[union-attr]

    async def test_spawn_can_reuse_name_after_cancel(self) -> None:
        tm = TaskManager()
        tm.spawn("t1", _hang_forever(), owner="test")
        result = await tm.cancel_and_await("test:t1", timeout=2.0)
        assert result is True
        assert tm.get_task("test:t1").status == "cancelled"  # type: ignore[union-attr]

        tm.spawn("t1", _noop(), owner="test")
        await asyncio.sleep(0.05)

        info = tm.get_task("test:t1")
        assert info is not None
        assert info.status == "done"

    async def test_cancel_by_owner(self) -> None:
        tm = TaskManager()
        tm.spawn("a", _hang_forever(), owner="plugin.x")
        tm.spawn("b", _hang_forever(), owner="plugin.x")
        tm.spawn("c", _hang_forever(), owner="plugin.y")

        cancelled = await tm.cancel_by_owner_and_await("plugin.x", timeout=2.0)
        assert len(cancelled) == 2
        assert tm.get_task("plugin.x:a").status == "cancelled"  # type: ignore[union-attr]
        assert tm.get_task("plugin.x:b").status == "cancelled"  # type: ignore[union-attr]
        # plugin.y's task should still be running
        assert tm.get_task("plugin.y:c").status == "running"  # type: ignore[union-attr]

        await tm.shutdown()

    async def test_cancel_by_owner_no_tasks(self) -> None:
        tm = TaskManager()
        cancelled = await tm.cancel_by_owner_and_await("nonexistent", timeout=1.0)
        assert cancelled == []


# ── Spawn Interval ─────────────────────────────────────────


class TestSpawnInterval:
    async def test_interval_runs_repeatedly(self) -> None:
        tm = TaskManager()
        counter = [0]
        tm.spawn_interval(
            "ticker",
            lambda: _count_calls(counter),
            owner="test",
            interval_seconds=0.05,
        )
        # Wait for a few ticks
        await asyncio.sleep(0.25)
        await tm.shutdown()

        # Should have run multiple times (at least 3)
        assert counter[0] >= 3

    async def test_interval_with_initial_delay(self) -> None:
        tm = TaskManager()
        counter = [0]
        tm.spawn_interval(
            "delayed",
            lambda: _count_calls(counter),
            owner="test",
            interval_seconds=0.05,
            initial_delay=0.15,
        )
        # During the initial delay, nothing should have run
        await asyncio.sleep(0.05)
        assert counter[0] == 0

        # After delay, should start running
        await asyncio.sleep(0.2)
        await tm.shutdown()
        assert counter[0] >= 2


# ── Spawn Reconnecting ─────────────────────────────────────


class TestSpawnReconnecting:
    async def test_reconnecting_retries_on_failure(self) -> None:
        tm = TaskManager()
        attempt = [0]

        async def _flaky() -> None:
            attempt[0] += 1
            if attempt[0] < 3:
                raise RuntimeError("not yet")

        tm.spawn_reconnecting(
            "flaky",
            _flaky,
            owner="test",
            initial_delay=0.05,
            max_delay=0.1,
            backoff_factor=2.0,
        )

        # Wait enough for retries
        await asyncio.sleep(0.5)
        await tm.shutdown()

        # Should have retried at least 3 times
        assert attempt[0] >= 3

    async def test_reconnecting_resets_backoff_on_success(self) -> None:
        tm = TaskManager()
        attempts = [0]

        async def _succeed_then_fail() -> None:
            attempts[0] += 1
            # Succeed first time, then fail
            if attempts[0] > 1:
                raise RuntimeError("later failure")

        tm.spawn_reconnecting(
            "reset-test",
            _succeed_then_fail,
            owner="test",
            initial_delay=0.05,
            max_delay=0.2,
        )

        await asyncio.sleep(0.3)
        await tm.shutdown()
        # Should have multiple attempts
        assert attempts[0] >= 2

    async def test_reconnecting_reports_each_failure_to_on_error(self) -> None:
        tm = TaskManager()
        errors: list[tuple[str, Exception]] = []

        async def _always_fail() -> None:
            raise RuntimeError("temporary")

        async def _on_error(name: str, exc: Exception) -> None:
            errors.append((name, exc))

        tm.spawn_reconnecting(
            "flaky",
            _always_fail,
            owner="test",
            initial_delay=0.05,
            max_delay=0.05,
            on_error=_on_error,
        )

        await asyncio.sleep(0.16)
        await tm.shutdown()

        assert errors
        assert all(name == "flaky" for name, _ in errors)
        assert all(isinstance(exc, RuntimeError) for _, exc in errors)


# ── Shutdown ────────────────────────────────────────────────


class TestShutdown:
    async def test_shutdown_cancels_all(self) -> None:
        tm = TaskManager()
        tm.spawn("t1", _hang_forever(), owner="a")
        tm.spawn("t2", _hang_forever(), owner="b")
        tm.spawn("t3", _hang_forever(), owner="c")

        await tm.shutdown(timeout=5.0)

        for key in ("a:t1", "b:t2", "c:t3"):
            info = tm.get_task(key)
            assert info is not None
            assert info.status in ("cancelled", "done")

    async def test_shutdown_empty(self) -> None:
        tm = TaskManager()
        await tm.shutdown()  # Should not raise


# ── Error handling ─────────────────────────────────────────


class TestErrorHandling:
    async def test_uncaught_exception_logged_and_status_failed(self) -> None:
        tm = TaskManager()
        tm.spawn("bad", _fail_once(), owner="test")
        await asyncio.sleep(0.05)

        info = tm.get_task("test:bad")
        assert info is not None
        assert info.status == "failed"
        assert "boom" in (info.error or "")

    async def test_on_error_callback_called(self) -> None:
        tm = TaskManager()
        errors: list[tuple[str, Exception]] = []

        async def _on_error(name: str, exc: Exception) -> None:
            errors.append((name, exc))

        tm.spawn("bad", _fail_once(), owner="test", on_error=_on_error)
        await asyncio.sleep(0.05)

        assert len(errors) == 1
        assert errors[0][0] == "bad"
        assert isinstance(errors[0][1], RuntimeError)

    async def test_on_error_exception_does_not_crash(self) -> None:
        tm = TaskManager()

        async def _broken_callback(name: str, exc: Exception) -> None:
            raise ValueError("callback itself fails")

        tm.spawn("bad", _fail_once(), owner="test", on_error=_broken_callback)
        # Should not raise, just log
        await asyncio.sleep(0.05)

        info = tm.get_task("test:bad")
        assert info is not None
        assert info.status == "failed"


# ── List / Query ───────────────────────────────────────────


class TestQuery:
    async def test_list_tasks_filter_by_owner(self) -> None:
        tm = TaskManager()
        tm.spawn("a", _hang_forever(), owner="x")
        tm.spawn("b", _hang_forever(), owner="x")
        tm.spawn("c", _hang_forever(), owner="y")

        x_tasks = tm.list_tasks(owner="x")
        assert len(x_tasks) == 2
        assert all(t.owner == "x" for t in x_tasks)

        y_tasks = tm.list_tasks(owner="y")
        assert len(y_tasks) == 1

        all_tasks = tm.list_tasks()
        assert len(all_tasks) == 3

        await tm.shutdown()

    async def test_get_task_nonexistent(self) -> None:
        tm = TaskManager()
        assert tm.get_task("nope:nope") is None

    async def test_task_info_fields(self) -> None:
        tm = TaskManager()
        tm.spawn("my-task", _hang_forever(), owner="test.owner", kind="interval")

        info = tm.get_task("test.owner:my-task")
        assert info is not None
        assert info.name == "my-task"
        assert info.owner == "test.owner"
        assert info.kind == "interval"
        assert info.status == "running"
        assert info.created_at is not None
        assert info.error is None

        await tm.shutdown()
