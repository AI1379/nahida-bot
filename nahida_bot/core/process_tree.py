"""Cross-platform process-tree signaling and hard-kill helpers.

Issue #57: subprocesses spawned via ``create_subprocess_shell`` run under a
shell, and shell commands are often pipelines (``python | tail``) or scripts
that fork children. Signalling only the spawned ``Process`` kills the shell
and orphans the grandchildren — exactly how a runaway agent script survived
both the exec timeout kill and the run cancellation and grew to 2.1 GB RSS
until the host OOM-killed.

The fix pattern everywhere:

1. spawn with ``start_new_session=True`` so the child becomes a session and
   process-group leader on POSIX;
2. on every abnormal exit path (timeout, cancellation, error) hard-kill the
   whole group via :func:`kill_process_tree` and reap it.

Windows exposes no process groups to Python; there we can only signal the
direct child and grandchildren survive. This is a documented limitation —
the production deployment runs Linux, Windows is the dev machine.

Safety: if a child was spawned *without* ``start_new_session=True`` it shares
our own process group, and a group kill would take the bot itself down. The
helpers detect that and fall back to signalling only the child.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from asyncio.subprocess import Process


def signal_process_tree(proc: Process, sig: int | signal.Signals) -> None:
    """Send ``sig`` to the tree led by ``proc`` (direct child on Windows).

    Best-effort: process-exit races are swallowed. On POSIX the signal goes
    to the child's whole process group when it leads one (see module docs),
    otherwise just to the child itself.
    """
    try:
        if sys.platform == "win32":
            proc.send_signal(sig)
            return
        pid = getattr(proc, "pid", None)
        if pid:
            pgid = os.getpgid(pid)
            if pgid != os.getpgid(0):
                os.killpg(pgid, sig)
                return
        # No usable pid (test doubles) or the child shares our own group
        # (spawned without start_new_session) — a group signal would hit
        # the bot itself, so fall back to signalling only the child.
        proc.send_signal(sig)
    except (ProcessLookupError, PermissionError, OSError):
        # Already reaped, or not ours to signal — nothing sensible left to do.
        pass


def signal_kill_tree(proc: Process) -> None:
    """Fire-and-forget SIGKILL of the tree led by ``proc``.

    Unlike :func:`kill_process_tree` this neither reaps nor closes pipes, for
    callers that manage the wait themselves (process_supervisor).
    """
    if sys.platform == "win32":
        # signal.SIGKILL does not exist on Windows; Process.kill() maps to
        # TerminateProcess on the direct child.
        try:
            proc.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass
        return
    signal_process_tree(proc, signal.SIGKILL)


async def kill_process_tree(
    proc: Process,
    *,
    reap_timeout: float = 5.0,
    close_pipes: bool = True,
) -> None:
    """SIGKILL the tree led by ``proc``, reap it, and close its pipes.

    A no-op (beyond pipe closing) when ``proc`` already exited, so it is safe
    to call from ``finally`` blocks covering timeout, cancellation, and error
    paths alike. Never raises: cleanup must not mask the original error.
    """
    if proc.returncode is None:
        signal_kill_tree(proc)
        try:
            await asyncio.wait_for(proc.wait(), timeout=reap_timeout)
        except (TimeoutError, ProcessLookupError):
            # Reaping stuck (should not happen after SIGKILL); the kill
            # itself already went out, which is the part that matters.
            pass
    if close_pipes:
        close_process_pipes(proc)


def close_process_pipes(proc: Process) -> None:
    """Close stdin/stdout/stderr pipe transports to release read buffers."""
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(proc, name, None)
        if stream is None:
            continue
        try:
            stream.close()
        except Exception:
            pass
