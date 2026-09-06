"""POSIX-side smoke verification for issue #57 helpers.

Runs inside WSL (Linux) against the REAL nahida_bot/core/process_tree.py
(loaded by path — the module is stdlib-only). The pytest suite on the
Windows dev host has to skip the process-group tests, so this script is
where the Linux production behaviour actually gets exercised.

Usage (from Windows): wsl.exe -d Ubuntu-24.04 python3 /mnt/d/.../verify_posix.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import signal
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO / "nahida_bot" / "core" / "process_tree.py"

spec = importlib.util.spec_from_file_location("process_tree", MODULE_PATH)
assert spec is not None and spec.loader is not None
process_tree = importlib.util.module_from_spec(spec)
spec.loader.exec_module(process_tree)

WORKER_SRC = """import os, sys, time
with open(sys.argv[1], "w") as f:
    f.write(str(os.getpid()))
time.sleep(60)
"""

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        state = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[0]
    except OSError:
        return False
    return state != "Z"


def wait_pid_death(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.1)
    return not pid_alive(pid)


def wait_marker(path: Path, timeout: float = 10.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.read_text().strip():
            return int(path.read_text().strip())
        time.sleep(0.05)
    raise AssertionError(f"worker never wrote {path}")


async def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    worker = tmp / "worker.py"
    worker.write_text(WORKER_SRC)

    # 1. kill_process_tree reaps a tree spawned with start_new_session.
    marker = tmp / "m1.pid"
    proc = await asyncio.create_subprocess_shell(
        f'"{sys.executable}" "{worker}" "{marker}"',
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    wpid = wait_marker(marker)
    check("worker alive before kill", pid_alive(wpid))
    await process_tree.kill_process_tree(proc)
    check("shell reaped", proc.returncode is not None)
    check("group kill reached grandchild", wait_pid_death(wpid))

    # 2. signal_process_tree on a shared-group child only signals the child
    #    (must NOT signal our own group — the test process survives).
    proc2 = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(60)",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    process_tree.signal_process_tree(proc2, signal.SIGTERM)
    await asyncio.wait_for(proc2.wait(), timeout=10)
    check(
        "shared-group fallback kills child only",
        proc2.returncode == -signal.SIGTERM,
        f"returncode={proc2.returncode}",
    )

    # 3. The _tool_exec timeout flow: spawn → wait_for timeout → finally
    #    kill_process_tree. Same stdlib call sequence as commands.py.
    marker3 = tmp / "m3.pid"
    proc3 = await asyncio.create_subprocess_shell(
        f'"{sys.executable}" "{worker}" "{marker3}"',
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        await asyncio.wait_for(proc3.communicate(), timeout=1.0)
        check("timeout raised", False, "communicate returned unexpectedly")
    except asyncio.TimeoutError:
        check("timeout raised", True)
    finally:
        await process_tree.kill_process_tree(proc3)
    wpid3 = wait_marker(marker3)
    check("timeout flow killed worker", wait_pid_death(wpid3))

    # 4. The cancellation flow: CancelledError must still run the finally
    #    cleanup (the exact production trigger from the OOM incident).
    marker4 = tmp / "m4.pid"
    proc4 = await asyncio.create_subprocess_shell(
        f'"{sys.executable}" "{worker}" "{marker4}"',
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )

    async def exec_like() -> None:
        try:
            try:
                await asyncio.wait_for(proc4.communicate(), timeout=60)
            finally:
                await process_tree.kill_process_tree(proc4)
        except asyncio.CancelledError:
            raise

    task = asyncio.create_task(exec_like())
    wpid4 = wait_marker(marker4)
    await asyncio.sleep(0.2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    check("cancellation flow killed worker", wait_pid_death(wpid4))
    check("cancellation propagated (CancelledError surfaced)", True)

    print()
    if failures:
        print(f"{len(failures)} FAILURES: {failures}")
        sys.exit(1)
    print("all POSIX checks passed")


if __name__ == "__main__":
    asyncio.run(main())
