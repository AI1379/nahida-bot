"""Shared local execution budget and asynchronous supervisor client."""

from __future__ import annotations

import asyncio
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import psutil


class ExecCommandError(RuntimeError):
    """A command failure the tool adapter must not classify as success."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ExecResult:
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    termination_reason: str = "completed"
    truncated: bool = False
    peak_rss_bytes: int = 0
    error: str = ""

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0 and self.termination_reason == "completed"

    @property
    def memory_limited(self) -> bool:
        return self.termination_reason == "memory_limit_exceeded"


class ExecRunner:
    """Application-owned budget shared by tools and scheduled commands.

    Memory is sampled by a separate process, including while the bot's event
    loop is blocked. This is accident protection, not a hard memory sandbox.
    """

    def __init__(
        self,
        *,
        memory_limit_mb: int = 512,
        max_concurrency: int = 1,
        min_available_memory_mb: int = 512,
        check_interval_seconds: float = 0.1,
        max_output_bytes: int = 50_000,
        max_processes: int = 64,
    ) -> None:
        for name, value, minimum in (
            ("memory_limit_mb", memory_limit_mb, 1),
            ("max_concurrency", max_concurrency, 1),
            ("min_available_memory_mb", min_available_memory_mb, 0),
            ("max_output_bytes", max_output_bytes, 1),
            ("max_processes", max_processes, 1),
        ):
            if type(value) is not int or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")
        if (
            not math.isfinite(check_interval_seconds)
            or not 0.01 <= check_interval_seconds <= 1
        ):
            raise ValueError("check_interval_seconds must be between 0.01 and 1")
        self.memory_limit_mb = memory_limit_mb
        self.min_available_memory_mb = min_available_memory_mb
        self._slots = asyncio.Semaphore(max_concurrency)
        self._limits = {
            "memory_bytes": memory_limit_mb * 1024 * 1024,
            "reserve_bytes": min_available_memory_mb * 1024 * 1024,
            "interval": check_interval_seconds,
            "output_bytes": max_output_bytes,
            "max_processes": max_processes,
        }

    async def run(
        self,
        command: str,
        *,
        timeout: float,
        cwd: str | None,
        env: dict[str, str] | None,
    ) -> ExecResult:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        if self._slots.locked():
            return ExecResult(
                termination_reason="busy", error="Execution concurrency limit reached"
            )
        async with self._slots:
            if psutil.virtual_memory().available < self._limits["reserve_bytes"]:
                return ExecResult(
                    termination_reason="host_memory_pressure",
                    error="Insufficient available host memory",
                )
            return await self._run(command, timeout=timeout, cwd=cwd, env=env)

    async def _run(
        self,
        command: str,
        *,
        timeout: float,
        cwd: str | None,
        env: dict[str, str] | None,
    ) -> ExecResult:
        # Only the command receives build_exec_env() and the workspace cwd.
        creation = asyncio.create_task(
            asyncio.create_subprocess_exec(
                sys.executable,
                "-E",
                "-s",
                str(Path(__file__).with_name("exec_worker.py")),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=0x08000000 if sys.platform == "win32" else 0,
                start_new_session=sys.platform != "win32",
            )
        )
        proc = None
        response = None
        try:
            # Retain the process even if cancellation arrives during creation.
            proc = await asyncio.shield(creation)
            assert proc.stdin is not None
            request = dict(
                command=command, timeout=timeout, cwd=cwd, env=env, limits=self._limits
            )
            proc.stdin.write((json.dumps(request) + "\n").encode())
            await proc.stdin.drain()
            response = asyncio.create_task(self._read_response(proc))
            raw, diagnostic = await asyncio.wait_for(
                asyncio.shield(response), timeout + 15
            )
            if proc.returncode != 0:
                return ExecResult(
                    termination_reason="supervisor_error",
                    error=diagnostic or f"Supervisor exited {proc.returncode}",
                )
            return ExecResult(**json.loads(raw))
        except asyncio.CancelledError:
            if proc is None:
                try:
                    proc = await creation
                except OSError:
                    pass
            raise
        except (OSError, ValueError, TypeError, TimeoutError) as exc:
            return ExecResult(termination_reason="supervisor_error", error=str(exc))
        finally:
            try:
                if proc is not None:
                    cleanup = asyncio.create_task(self._close_supervisor(proc))
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        await cleanup
                        raise
            finally:
                if response is not None:
                    response.cancel()
                    await asyncio.gather(response, return_exceptions=True)

    async def _read_response(self, proc: asyncio.subprocess.Process) -> tuple[str, str]:
        assert proc.stdout is not None and proc.stderr is not None
        # JSON may escape each captured byte into six ASCII characters.
        budget = self._limits["output_bytes"] * 12 + 65_536

        async def read(stream: asyncio.StreamReader) -> str:
            data = bytearray()
            while chunk := await stream.read(16_384):
                if len(data) + len(chunk) > budget:
                    raise ValueError("Supervisor response exceeds protocol budget")
                data.extend(chunk)
            return data.decode("utf-8", errors="replace")

        readers = [
            asyncio.create_task(read(proc.stdout)),
            asyncio.create_task(read(proc.stderr)),
        ]
        try:
            out, err = await asyncio.gather(*readers)
            await proc.wait()
            return out, err
        finally:
            for reader in readers:
                reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)

    @staticmethod
    async def _close_supervisor(proc: asyncio.subprocess.Process) -> None:
        # EOF also reaches the supervisor when the bot crashes.
        if proc.stdin is not None:
            proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), 8)
        except TimeoutError:
            from nahida_bot.core.exec_process import kill_descendants

            try:
                await asyncio.to_thread(kill_descendants, proc.pid)
            finally:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await asyncio.wait_for(proc.wait(), 3)
