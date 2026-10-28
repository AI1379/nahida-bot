"""Synchronous process ownership used only by the local exec supervisor."""

from __future__ import annotations

import os
import importlib
import signal
import subprocess
import sys
from typing import Any

import psutil


class WindowsJob:
    """Job lifetime containment; RSS is monitored separately on every OS."""

    def __init__(self) -> None:
        win32job = importlib.import_module("win32job")
        self.api = win32job
        self.handle: Any = win32job.CreateJobObject(None, "")
        info = win32job.QueryInformationJobObject(
            self.handle, win32job.JobObjectExtendedLimitInformation
        )
        info["BasicLimitInformation"]["LimitFlags"] = (
            win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        win32job.SetInformationJobObject(
            self.handle, win32job.JobObjectExtendedLimitInformation, info
        )

    def assign(self, pid: int) -> None:
        win32api = importlib.import_module("win32api")
        win32con = importlib.import_module("win32con")

        handle = win32api.OpenProcess(
            win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, pid
        )
        try:
            self.api.AssignProcessToJobObject(self.handle, handle)
        finally:
            handle.Close()

    def close(self) -> None:
        if self.handle is not None:
            try:
                self.api.TerminateJobObject(self.handle, 1)
            finally:
                self.handle.Close()
                self.handle = None


def kill_descendants(pid: int) -> None:
    """Emergency cleanup if a supervisor cannot respond to its control pipe."""
    try:
        root = psutil.Process(pid)
        children = root.children(recursive=True)
        for process in reversed(children):
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
    except psutil.NoSuchProcess:
        pass


class ManagedCommand:
    def __init__(self, command: str, cwd: str | None, env: dict[str, str] | None):
        self.job = WindowsJob() if sys.platform == "win32" else None
        self.proc = None
        try:
            self.proc = subprocess.Popen(
                command,
                shell=True,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=(0x08000000 | 0x00000004) if self.job else 0,
                start_new_session=sys.platform != "win32",
            )
            self.root = psutil.Process(self.proc.pid)
            self.known = {self.root}
            if self.job:
                # cmd.exe cannot execute or fork until Job assignment succeeds.
                self.job.assign(self.proc.pid)
                self.root.resume()
        except Exception:
            if self.job:
                self.job.close()
            if self.proc:
                self.proc.kill()
                self.proc.wait()
            raise
        assert self.proc.stdout is not None and self.proc.stderr is not None
        self.stdout = self.proc.stdout
        self.stderr = self.proc.stderr

    def sample(self) -> tuple[int, int]:
        # psutil identities include creation time. Keep discovered descendants
        # after reparenting, and let is_running/kill guard against PID reuse.
        queue = list(self.known)
        visited = set()
        rss = count = 0
        while queue:
            process = queue.pop()
            if process in visited:
                continue
            visited.add(process)
            try:
                if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                    if process != self.root:
                        self.known.discard(process)
                    continue
                children = process.children()
                self.known.update(children)
                queue.extend(children)
                rss += process.memory_info().rss
                count += 1
            except psutil.NoSuchProcess:
                if process != self.root:
                    self.known.discard(process)
                continue
        return rss, count

    def finished(self) -> bool:
        # Do not reap the POSIX leader before cleanup: its unreaped PID reserves
        # the group identity even when the shell exits before its descendants.
        assert self.proc is not None
        if self.job:
            return self.proc.poll() is not None
        try:
            return self.root.status() in {psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD}
        except psutil.NoSuchProcess:
            return True

    def close(self) -> int:
        assert self.proc is not None
        try:
            self.sample()
        finally:
            if self.job:
                self.job.close()
            elif sys.platform != "win32" and self.proc.returncode is None:
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            for process in self.known:
                if process == self.root:
                    continue
                try:
                    process.kill()
                except psutil.NoSuchProcess:
                    pass
            code = self.proc.wait(timeout=3)
            _, alive = psutil.wait_procs(
                [p for p in self.known if p != self.root], timeout=2
            )
            if any(
                p.is_running() and p.status() != psutil.STATUS_ZOMBIE for p in alive
            ):
                raise RuntimeError("Command descendants survived cleanup")
        return code
