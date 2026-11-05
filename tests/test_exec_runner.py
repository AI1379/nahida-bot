"""Real local process regression tests, run on Windows, Linux and macOS."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import psutil
import pytest

from nahida_bot.core import exec_runner, exec_worker, exec_process
from nahida_bot.core.exec_runner import ExecRunner, ExecResult


def script(tmp_path: Path, source: str) -> str:
    path = tmp_path / f"worker_{uuid4().hex}.py"
    path.write_text(source, encoding="utf-8")
    argv = [sys.executable, str(path)]
    return (
        subprocess.list2cmdline(argv) if sys.platform == "win32" else shlex.join(argv)
    )


async def marker_pid(path: Path) -> int:
    async with asyncio.timeout(10):
        while True:
            if path.exists() and path.read_text().strip():
                return int(path.read_text())
            await asyncio.sleep(0.02)


def alive(pid: int) -> bool:
    try:
        p = psutil.Process(pid)
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


async def dead(pid: int) -> None:
    async with asyncio.timeout(6):
        while alive(pid):
            await asyncio.sleep(0.02)


@pytest.fixture
def runner():
    return ExecRunner(
        max_concurrency=1, min_available_memory_mb=0, check_interval_seconds=0.02
    )


async def test_bounded_output_and_nonzero_exit(runner, tmp_path):
    result = await runner.run(
        script(
            tmp_path,
            "import sys; print('x'*1000000); print('y'*1000000,file=sys.stderr); sys.exit(7)",
        ),
        timeout=10,
        cwd=None,
        env=None,
    )
    assert result.returncode == 7, result
    assert result.termination_reason == "exit_error"
    assert result.truncated
    assert len(result.stdout) == len(result.stderr) == 50000


async def test_cwd_environment_and_unicode(runner, tmp_path):
    cwd = tmp_path / "space directory"
    cwd.mkdir()
    env = dict(os.environ, EXEC_TEST_VALUE="spaces $literal %literal% 中文")
    result = await runner.run(
        script(
            tmp_path,
            "import os,json; print(json.dumps([os.getcwd(),os.environ['EXEC_TEST_VALUE']]))",
        ),
        timeout=10,
        cwd=str(cwd),
        env=env,
    )
    assert result.succeeded, result
    assert json.loads(result.stdout) == [str(cwd), env["EXEC_TEST_VALUE"]]


async def test_two_real_commands_can_rendezvous_concurrently(tmp_path):
    runner = ExecRunner(max_concurrency=2, min_available_memory_mb=0)
    markers = [tmp_path / f"ready_{index}" for index in range(2)]

    def source(index):
        return (
            "from pathlib import Path\nimport time\n"
            f"Path({str(markers[index])!r}).write_text('ready')\n"
            "deadline=time.monotonic()+5\n"
            f"while not Path({str(markers[1 - index])!r}).exists():\n"
            "    if time.monotonic()>deadline: raise RuntimeError('other command never started')\n"
            "    time.sleep(0.01)\nprint('both running')\n"
        )

    results = await asyncio.gather(
        *[
            runner.run(script(tmp_path, source(index)), timeout=10, cwd=None, env=None)
            for index in range(2)
        ]
    )
    assert all(result.succeeded and result.started is True for result in results), (
        results
    )
    assert runner.admission.snapshot["running"] == 0


async def test_memory_limit_counts_children_and_recovers(tmp_path):
    runner = ExecRunner(
        memory_limit_mb=96, min_available_memory_mb=0, check_interval_seconds=0.02
    )
    allocate = "import time; a=bytearray(40*1024*1024); time.sleep(30)"
    source = f"import subprocess,sys; ps=[subprocess.Popen([sys.executable,'-c',{allocate!r}]) for _ in range(2)]; [p.wait() for p in ps]"
    result = await runner.run(script(tmp_path, source), timeout=10, cwd=None, env=None)
    assert result.memory_limited, result
    assert result.peak_rss_bytes > 96 * 1024 * 1024
    recovered = await runner.run(
        script(tmp_path, "print(42)"), timeout=10, cwd=None, env=None
    )
    assert recovered.succeeded and recovered.stdout.strip() == "42", recovered


@pytest.mark.parametrize("ending", ["timeout", "cancel", "normal"])
async def test_cleans_detached_descendants(runner, tmp_path, ending):
    marker = tmp_path / "child.pid"
    child = f"import os,time; open({str(marker)!r},'w').write(str(os.getpid())); time.sleep(30)"
    source = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}],start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); time.sleep(0.6)"
    if ending != "normal":
        source += "; time.sleep(30)"
    task = asyncio.create_task(
        runner.run(
            script(tmp_path, source),
            timeout=2 if ending == "timeout" else 10,
            cwd=None,
            env=None,
        )
    )
    try:
        pid = await marker_pid(marker)
        if ending == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            result = await task
            assert result.termination_reason == (
                "timeout" if ending == "timeout" else "completed"
            ), result
        await dead(pid)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_monitor_keeps_running_when_bot_event_loop_is_blocked(tmp_path):
    marker = tmp_path / "allocator.pid"
    source = f"import os,time; open({str(marker)!r},'w').write(str(os.getpid())); time.sleep(0.2); a=bytearray(96*1024*1024); time.sleep(30)"
    runner = ExecRunner(
        memory_limit_mb=64, min_available_memory_mb=0, check_interval_seconds=0.02
    )
    task = asyncio.create_task(
        runner.run(script(tmp_path, source), timeout=10, cwd=None, env=None)
    )
    try:
        pid = await marker_pid(marker)
        time.sleep(1.2)  # deliberately prevent the client from polling/killing
        assert not alive(pid)
        assert (await task).memory_limited
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_supervisor_cleans_up_after_parent_process_crash(tmp_path):
    marker = tmp_path / "orphan.pid"
    source = f"import os,time; open({str(marker)!r},'w').write(str(os.getpid())); time.sleep(30)"
    command = script(tmp_path, source)
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import asyncio\nfrom nahida_bot.core.exec_runner import ExecRunner\n"
        + f"asyncio.run(ExecRunner(min_available_memory_mb=0).run({command!r},timeout=30,cwd=None,env=None))\n"
    )
    env = dict(
        os.environ, PYTHONPATH=str(Path(exec_runner.__file__).resolve().parents[2])
    )
    parent = await asyncio.create_subprocess_exec(
        sys.executable,
        str(driver),
        env=env,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        pid = await marker_pid(marker)
        parent.kill()
        await parent.wait()
        await dead(pid)
    finally:
        if parent.returncode is None:
            parent.kill()
            await parent.wait()


async def test_cancellation_during_supervisor_creation_does_not_leak(
    monkeypatch, runner
):
    original = asyncio.create_subprocess_exec
    created, release = asyncio.Event(), asyncio.Event()
    processes = []

    async def delayed(*args, **kwargs):
        p = await original(*args, **kwargs)
        processes.append(p)
        created.set()
        await release.wait()
        return p

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed)
    task = asyncio.create_task(
        runner.run("never executed", timeout=5, cwd=None, env=None)
    )
    await created.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert processes[0].returncode == 0


async def test_repeated_cancellation_keeps_slot_until_process_cleanup(
    runner, tmp_path, monkeypatch
):
    marker = tmp_path / "cleanup.pid"
    source = f"import os,time; open({str(marker)!r},'w').write(str(os.getpid())); time.sleep(60)"
    close_started, release = asyncio.Event(), asyncio.Event()
    original = runner._close_supervisor

    async def delayed_close(proc):
        close_started.set()
        await release.wait()
        await original(proc)

    monkeypatch.setattr(runner, "_close_supervisor", delayed_close)
    task = asyncio.create_task(
        runner.run(script(tmp_path, source), timeout=30, cwd=None, env=None)
    )
    try:
        pid = await marker_pid(marker)
        task.cancel()
        await close_started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert runner.admission.snapshot["running"] == 1
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await dead(pid)
        assert runner.admission.snapshot["running"] == 0
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_concurrency_queues_until_cleanup_releases_slot(monkeypatch, runner):
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        await release.wait()
        return ExecResult(0)

    monkeypatch.setattr(runner, "_run", blocked)
    task = asyncio.create_task(runner.run("first", timeout=5, cwd=None, env=None))
    await entered.wait()
    waiting = asyncio.create_task(runner.run("excess", timeout=5, cwd=None, env=None))
    try:
        await asyncio.sleep(0)
        assert not waiting.done()
        assert runner.admission.snapshot["queued"] == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    release.set()
    assert (await waiting).succeeded
    assert (await runner.run("next", timeout=5, cwd=None, env=None)).succeeded


async def test_low_host_memory_refuses_to_spawn(monkeypatch):
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(available=0))
    runner = ExecRunner()
    spawn = AsyncMock()
    monkeypatch.setattr(runner, "_run", spawn)
    result = await runner.run("never executed", timeout=5, cwd=None, env=None)
    assert result.termination_reason == "host_memory_pressure"
    spawn.assert_not_called()


@pytest.mark.parametrize(
    "config",
    [
        {"memory_limit_mb": 0},
        {"max_concurrency": False},
        {"check_interval_seconds": float("nan")},
        {"check_interval_seconds": 0},
        {"max_output_bytes": 0},
    ],
)
def test_invalid_limits_are_rejected(config):
    with pytest.raises(ValueError):
        ExecRunner(**config)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job assignment")
def test_job_assignment_failure_never_runs_command(tmp_path, monkeypatch):
    marker = tmp_path / "must-not-run"
    proc_list = []
    original = subprocess.Popen

    def capture(*args, **kwargs):
        p = original(*args, **kwargs)
        proc_list.append(p)
        return p

    def fail(*args):
        raise OSError("job assignment refused")

    monkeypatch.setattr(subprocess, "Popen", capture)
    monkeypatch.setattr(exec_process.WindowsJob, "assign", fail)
    command = script(tmp_path, f"open({str(marker)!r},'w').write('bad')")
    with pytest.raises(OSError, match="job assignment refused"):
        exec_process.ManagedCommand(command, None, None)
    assert not marker.exists()
    assert proc_list[0].poll() is not None


def test_running_host_memory_pressure_stops_owned_task(monkeypatch):
    import io
    import threading

    values = iter([2**40, 1])
    monkeypatch.setattr(
        psutil, "virtual_memory", lambda: SimpleNamespace(available=next(values))
    )
    closed = []
    fake = SimpleNamespace(
        stdout=io.BytesIO(),
        stderr=io.BytesIO(),
        sample=lambda: (0, 1),
        close=lambda: closed.append(True) or 1,
    )
    monkeypatch.setattr(exec_worker, "ManagedCommand", lambda *args: fake)
    result = exec_worker.supervise(
        dict(
            command="fake",
            cwd=None,
            env=None,
            timeout=5,
            limits=dict(
                memory_bytes=100,
                reserve_bytes=100,
                interval=0.1,
                output_bytes=10,
                max_processes=10,
            ),
        ),
        threading.Event(),
    )
    assert result["termination_reason"] == "host_memory_pressure"
    assert closed


def test_output_capture_errors_are_observable():
    import io

    class BrokenPipe(io.BytesIO):
        def read(self, size=-1):
            raise OSError("read failed")

    capture = exec_worker.OutputCapture(BrokenPipe(), 100)
    assert capture.finish() == ("", False)
    assert "read failed" in capture.error
