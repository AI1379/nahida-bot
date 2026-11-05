"""Local supervisor entry point, independent of the bot runtime."""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from typing import Any, IO

import psutil

if __package__:
    from .exec_process import ManagedCommand
else:
    from exec_process import ManagedCommand


class OutputCapture:
    """Drain continuously while retaining a bounded prefix."""

    def __init__(self, stream: IO[bytes], limit: int) -> None:
        self.stream = stream
        self.limit = limit
        self.data = bytearray()
        self.truncated = False
        self.error = ""
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self) -> None:
        try:
            while chunk := self.stream.read(16_384):
                with self.lock:
                    remaining = self.limit - len(self.data)
                    self.data.extend(chunk[:remaining])
                    self.truncated |= len(chunk) > remaining
        except (OSError, ValueError) as exc:
            self.error = f"Output capture failed: {exc}"

    def finish(self) -> tuple[str, bool]:
        self.thread.join(timeout=1)
        if self.thread.is_alive():
            self.error = "Command output pipe remained open after cleanup"
        with self.lock:
            return self.data.decode(
                "utf-8", errors="replace"
            ), self.truncated or self.thread.is_alive()


def watch_parent(cancelled: threading.Event) -> None:
    # stdin belongs only to this control link, never to the command.
    os.read(sys.stdin.fileno(), 1)
    cancelled.set()


def stop_reason(
    command: ManagedCommand,
    limits: dict[str, Any],
    deadline: float,
    cancelled: threading.Event,
    rss: int,
    count: int,
) -> str | None:
    """Choose one terminal cause; do not infer OOM from an exit code."""
    if cancelled.is_set():
        return "parent_disconnected"
    if rss > limits["memory_bytes"]:
        return "memory_limit_exceeded"
    if count > limits["max_processes"]:
        return "process_limit_exceeded"
    if psutil.virtual_memory().available < limits["reserve_bytes"]:
        return "host_memory_pressure"
    if time.monotonic() >= deadline:
        return "timeout"
    return "completed" if command.finished() else None


def supervise(request: dict[str, Any], cancelled: threading.Event) -> dict[str, Any]:
    limits = request["limits"]
    result: dict[str, Any] = dict(
        returncode=None,
        stdout="",
        stderr="",
        termination_reason="completed",
        truncated=False,
        peak_rss_bytes=0,
        error="",
        started=False,
        cleanup_succeeded=True,
    )
    command = None
    captures: list[OutputCapture] = []
    deadline = time.monotonic() + request["timeout"]
    try:
        if cancelled.is_set():
            result["termination_reason"] = "parent_disconnected"
            return result
        if psutil.virtual_memory().available < limits["reserve_bytes"]:
            result["termination_reason"] = "host_memory_pressure"
            return result
        command = ManagedCommand(request["command"], request["cwd"], request["env"])
        result["started"] = True
        captures = [
            OutputCapture(stream, limits["output_bytes"])
            for stream in (command.stdout, command.stderr)
        ]
        while True:
            rss, count = command.sample()
            result["peak_rss_bytes"] = max(result["peak_rss_bytes"], rss)
            reason = stop_reason(command, limits, deadline, cancelled, rss, count)
            if reason is not None:
                result["termination_reason"] = reason
                break
            cancelled.wait(limits["interval"])
    except Exception as exc:
        result["termination_reason"] = "supervisor_error" if command else "spawn_error"
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if command is not None:
            try:
                result["returncode"] = command.close()
            except Exception as exc:
                result["cleanup_succeeded"] = False
                result["termination_reason"] = "cleanup_failed"
                result["error"] = f"{type(exc).__name__}: {exc}"
        for key, capture in zip(("stdout", "stderr"), captures):
            result[key], truncated = capture.finish()
            result["truncated"] |= truncated
            if capture.error:
                result["error"] = capture.error
                if result["termination_reason"] == "completed":
                    result["termination_reason"] = "cleanup_failed"
    if result["termination_reason"] == "completed" and result["returncode"]:
        result["termination_reason"] = "exit_error"
    return result


def main() -> int:
    raw = sys.stdin.buffer.readline(1_048_576)
    if not raw:
        return 0
    if not raw.endswith(b"\n"):
        raise ValueError("Execution request exceeds protocol budget")
    request = json.loads(raw)
    cancelled = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: cancelled.set())
    signal.signal(signal.SIGINT, lambda *_: cancelled.set())
    threading.Thread(target=watch_parent, args=(cancelled,), daemon=True).start()
    result = supervise(request, cancelled)
    # Command output cannot impersonate this control response.
    print(json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
