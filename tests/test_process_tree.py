"""Process-tree hard-recycling tests (issue #57).

Covers the ``exec`` tool and scheduler script executor leak: when a spawned
shell command is timed out or its caller cancelled, the whole process tree
must be SIGKILLed and reaped instead of orphaning the shell's children.
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import signal
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from nahida_bot.core.process_tree import (
    close_process_pipes,
    kill_process_tree,
    signal_process_tree,
)
from nahida_bot.plugins.builtin.commands import BuiltinCommandsPlugin
from nahida_bot.scheduler.script_executor import execute_script
from tests.test_builtin_commands_plugin import _FakeAPI, _manifest
from tests.test_scheduler import _job

_IS_WIN = sys.platform == "win32"

# Worker script: writes its pid to a marker file, then sleeps long enough
# that only a kill can end it.
_WORKER_SRC = """import os, sys, time
with open(sys.argv[1], "w") as f:
    f.write(str(os.getpid()))
time.sleep(60)
"""


def _pid_alive(pid: int) -> bool:
    if _IS_WIN:
        synchronize = 0x00100000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return False
        try:
            wait_timeout = 0x102
            return kernel32.WaitForSingleObject(handle, 0) == wait_timeout
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # os.kill succeeds on zombies too; an unreaped zombie is dead for our
    # purposes (it holds no memory).
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as stat:
            state = stat.read().rsplit(")", 1)[1].split()[0]
    except OSError:
        return False
    return state != "Z"


async def _wait_for_worker_pid(marker: Path, timeout: float = 10.0) -> int:
    """Wait until the spawned worker has written its pid to ``marker``."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if marker.exists():
            text = marker.read_text(encoding="utf-8").strip()
            if text:
                return int(text)
        await asyncio.sleep(0.05)
    raise AssertionError(f"worker did not write its pid to {marker}")


async def _wait_for_pid_death(pid: int, timeout: float = 10.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if not _pid_alive(pid):
            return True
        await asyncio.sleep(0.1)
    return not _pid_alive(pid)


def _plugin(tmp_path: Path) -> BuiltinCommandsPlugin:
    api = _FakeAPI()
    api.workspace_root = tmp_path  # exec resolves cwd against the workspace
    return BuiltinCommandsPlugin(api=api, manifest=_manifest())


def _script_job(script_command: str, timeout: float):
    return replace(
        _job(),
        executor_type="script_then_agent",
        script_command=script_command,
        script_timeout_seconds=timeout,
    )


# ── kill_process_tree helper ───────────────────────────────


@pytest.mark.asyncio
async def test_kill_process_tree_reaps_direct_child(tmp_path: Path) -> None:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(60)",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await kill_process_tree(proc)
    assert proc.returncode is not None
    assert await _wait_for_pid_death(proc.pid)
    close_process_pipes(proc)  # idempotent


@pytest.mark.asyncio
async def test_kill_process_tree_is_noop_after_normal_exit() -> None:
    proc = await asyncio.create_subprocess_exec(sys.executable, "-c", "print('hi')")
    await proc.wait()
    await kill_process_tree(proc)  # must not raise or resurrect anything
    assert proc.returncode == 0


@pytest.mark.asyncio
@pytest.mark.skipif(_IS_WIN, reason="no process groups on Windows")
async def test_kill_process_tree_kills_shell_grandchildren(
    tmp_path: Path,
) -> None:
    """The #57 shape: ``sh → worker`` must die together with the shell."""
    worker = tmp_path / "worker.py"
    marker = tmp_path / "worker.pid"
    worker.write_text(_WORKER_SRC, encoding="utf-8")
    proc = await asyncio.create_subprocess_shell(
        f'"{sys.executable}" "{worker}" "{marker}"',
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    worker_pid = await _wait_for_worker_pid(marker)
    assert _pid_alive(worker_pid)

    await kill_process_tree(proc)
    assert proc.returncode is not None
    assert await _wait_for_pid_death(worker_pid), "grandchild survived the group kill"


@pytest.mark.asyncio
@pytest.mark.skipif(_IS_WIN, reason="POSIX process-group semantics")
async def test_signal_process_tree_never_signals_own_group() -> None:
    """A child spawned WITHOUT start_new_session shares our group.

    Signalling the group would kill the test (and, in production, the bot)
    itself; the helper must fall back to the child only.
    """
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(60)",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    signal_process_tree(proc, signal.SIGTERM)
    await asyncio.wait_for(proc.wait(), timeout=10)
    assert proc.returncode == -15


# ── exec tool ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_exec_success_captures_output(tmp_path: Path) -> None:
    plugin = _plugin(tmp_path)
    result = await plugin._tool_exec(
        f'"{sys.executable}" -c "print(\'hello\')"', timeout=10
    )
    assert result.startswith("Exit code: 0")
    assert "hello" in result


@pytest.mark.asyncio
async def test_tool_exec_timeout_returns_message(tmp_path: Path) -> None:
    plugin = _plugin(tmp_path)
    result = await plugin._tool_exec(
        f'"{sys.executable}" -c "import time; time.sleep(60)"', timeout=1
    )
    assert result.startswith("Command timed out after 1s.")


@pytest.mark.asyncio
@pytest.mark.skipif(_IS_WIN, reason="no process groups on Windows")
async def test_tool_exec_timeout_kills_whole_tree(tmp_path: Path) -> None:
    """Timeout must recycle the spawned tree, not just the shell (#57)."""
    worker = tmp_path / "worker.py"
    marker = tmp_path / "worker.pid"
    worker.write_text(_WORKER_SRC, encoding="utf-8")
    plugin = _plugin(tmp_path)

    result = await plugin._tool_exec(
        f'"{sys.executable}" "{worker}" "{marker}"', timeout=1
    )
    assert result.startswith("Command timed out after 1s.")
    worker_pid = await _wait_for_worker_pid(marker)
    assert await _wait_for_pid_death(worker_pid), (
        "worker survived the exec timeout kill"
    )


@pytest.mark.asyncio
@pytest.mark.skipif(_IS_WIN, reason="no process groups on Windows")
async def test_tool_exec_cancellation_kills_whole_tree(tmp_path: Path) -> None:
    """The production trigger: run replaced mid-tool, exec task cancelled.

    The CancelledError used to fly straight past every cleanup line and
    leak the whole subprocess tree until the host OOMed.
    """
    worker = tmp_path / "worker.py"
    marker = tmp_path / "worker.pid"
    worker.write_text(_WORKER_SRC, encoding="utf-8")
    plugin = _plugin(tmp_path)

    task = asyncio.create_task(
        plugin._tool_exec(f'"{sys.executable}" "{worker}" "{marker}"', timeout=60)
    )
    worker_pid = await _wait_for_worker_pid(marker)
    assert _pid_alive(worker_pid)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await _wait_for_pid_death(worker_pid), (
        "worker survived the cancellation kill"
    )


# ── scheduler script executor ──────────────────────────────


@pytest.mark.asyncio
async def test_execute_script_timeout_returns_result(tmp_path: Path) -> None:
    result = await execute_script(
        _script_job(f'"{sys.executable}" -c "import time; time.sleep(60)"', 0.5)
    )
    assert result.timed_out is True
    assert result.succeeded is False


@pytest.mark.asyncio
@pytest.mark.skipif(_IS_WIN, reason="no process groups on Windows")
async def test_execute_script_timeout_kills_whole_tree(tmp_path: Path) -> None:
    worker = tmp_path / "worker.py"
    marker = tmp_path / "worker.pid"
    worker.write_text(_WORKER_SRC, encoding="utf-8")

    result = await execute_script(
        _script_job(f'"{sys.executable}" "{worker}" "{marker}"', 0.5)
    )
    assert result.timed_out is True
    worker_pid = await _wait_for_worker_pid(marker)
    assert await _wait_for_pid_death(worker_pid), (
        "worker survived the script timeout kill"
    )


@pytest.mark.asyncio
@pytest.mark.skipif(_IS_WIN, reason="no process groups on Windows")
async def test_execute_script_cancellation_kills_whole_tree(
    tmp_path: Path,
) -> None:
    worker = tmp_path / "worker.py"
    marker = tmp_path / "worker.pid"
    worker.write_text(_WORKER_SRC, encoding="utf-8")

    task = asyncio.create_task(
        execute_script(_script_job(f'"{sys.executable}" "{worker}" "{marker}"', 60))
    )
    worker_pid = await _wait_for_worker_pid(marker)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await _wait_for_pid_death(worker_pid), (
        "worker survived the script cancellation kill"
    )
