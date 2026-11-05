"""Shared local execution budget and asynchronous supervisor client."""

from __future__ import annotations

import asyncio
import json
import math
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from time import monotonic
from typing import Any

import psutil

from nahida_bot.core.config import ExecutionConfig
from nahida_bot.core.context import current_session
from nahida_bot.core.task_runtime import (
    AdmissionError,
    ExecutionAdmission,
    remaining_seconds,
)
from nahida_bot.core.task_runtime.admission import ServiceClass

EXEC_CLEANUP_BUDGET_SECONDS = 30.0
_MAX_REQUEST_BYTES = 1_048_576


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
    started: bool | None = None
    queue_wait_seconds: float = 0.0
    cleanup_succeeded: bool | None = None
    execution_timeout_seconds: float | None = None

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
        config: ExecutionConfig | None = None,
        **overrides: Any,
    ) -> None:
        # Keep keyword construction for existing embedders; validate in one model.
        values = config.model_dump() if config is not None else {}
        policy = ExecutionConfig(**(values | overrides))
        self.memory_limit_mb = policy.memory_limit_mb
        self.min_available_memory_mb = policy.min_available_memory_mb
        self.queue_timeout_seconds = policy.queue_timeout_seconds
        self.default_timeout_seconds = policy.default_timeout_seconds
        self.max_timeout_seconds = policy.max_timeout_seconds
        self.admission = ExecutionAdmission(
            policy.max_concurrency, policy.max_queued, policy.max_queued_per_owner
        )
        self._processes: dict[object, psutil.Process | None] = {}
        self._limits = {
            "memory_bytes": policy.memory_limit_mb * 1024 * 1024,
            "reserve_bytes": policy.min_available_memory_mb * 1024 * 1024,
            "interval": policy.check_interval_seconds,
            "output_bytes": policy.max_output_bytes,
            "max_processes": policy.max_processes,
        }

    async def run(
        self,
        command: str,
        *,
        timeout: float,
        cwd: str | None,
        env: dict[str, str] | None,
        owner: str | None = None,
        service_class: ServiceClass | None = None,
    ) -> ExecResult:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        if (
            len(command) > _MAX_REQUEST_BYTES
            or len(self._request(command, timeout=timeout, cwd=cwd, env=env))
            >= _MAX_REQUEST_BYTES
        ):
            return ExecResult(
                termination_reason="request_too_large",
                started=False,
                error="Execution request exceeds the 1 MiB supervisor budget",
            )
        owner, service_class = self._submission_identity(owner, service_class)
        queued_at = monotonic()
        remaining = remaining_seconds()
        queue_budget = (
            min(self.queue_timeout_seconds, remaining)
            if remaining is not None
            else self.queue_timeout_seconds
        )
        try:
            async with self.admission.slot(owner, service_class, queue_budget) as lease:
                waited = monotonic() - queued_at
                result = await self._run_reserved(
                    command,
                    timeout=timeout,
                    cwd=cwd,
                    env=env,
                    lease=lease,
                )
                return replace(result, queue_wait_seconds=waited)
        except AdmissionError as exc:
            return ExecResult(
                termination_reason=exc.code,
                error=str(exc),
                started=False,
                queue_wait_seconds=monotonic() - queued_at,
            )

    @staticmethod
    def _submission_identity(
        owner: str | None,
        service_class: ServiceClass | None,
    ) -> tuple[str, ServiceClass]:
        context = current_session.get()
        owner = owner or (
            context.actor_account_key or context.session_id if context else "internal"
        )
        if service_class is None:
            background = context is not None and (
                context.platform == "agent"
                or context.origin in {"cron_trigger", "subagent_task", "proactive_join"}
            )
            service_class = "background" if background else "interactive"
        return owner, service_class

    async def _run_reserved(
        self,
        command: str,
        *,
        timeout: float,
        cwd: str | None,
        env: dict[str, str] | None,
        lease: object,
    ) -> ExecResult:
        remaining = remaining_seconds()
        if remaining is not None:
            # Leave a bounded share for teardown even on a short parent deadline.
            timeout = min(timeout, remaining - min(15.0, remaining * 0.1))
        if timeout <= 0:
            return ExecResult(
                termination_reason="queue_timeout",
                started=False,
                error="Parent deadline expired before execution",
            )
        if not self._memory_available():
            return ExecResult(
                termination_reason="host_memory_pressure",
                started=False,
                error="Insufficient host memory for another execution reservation",
            )
        self._processes[lease] = None
        try:
            result = await self._run(
                command, timeout=timeout, cwd=cwd, env=env, reservation=lease
            )
            if (
                result.termination_reason in {"cleanup_failed", "supervisor_error"}
                and result.cleanup_succeeded is not True
            ):
                self.admission.quarantine(lease)
            return replace(result, execution_timeout_seconds=timeout)
        except Exception:
            self.admission.quarantine(lease)
            raise
        finally:
            if not self.admission.is_quarantined(lease):
                self._processes.pop(lease, None)

    def resolve_tool_timeout(self, requested: float | None = None) -> float:
        value = self.default_timeout_seconds if requested is None else requested
        if not math.isfinite(value):
            raise ValueError("timeout must be finite")
        return min(max(value, 1.0), self.max_timeout_seconds)

    def tool_budget_seconds(self, requested: float | None = None) -> float:
        return (
            self.resolve_tool_timeout(requested)
            + self.queue_timeout_seconds
            + EXEC_CLEANUP_BUDGET_SECONDS
        )

    def _memory_available(self) -> bool:
        """Account for reservations not yet reflected in the host's available RSS."""
        if not self.min_available_memory_mb:
            return True
        uncommitted = self._limits["memory_bytes"]
        for process in self._processes.values():
            rss = 0
            if process is not None:
                try:
                    rss = sum(
                        child.memory_info().rss
                        for child in process.children(recursive=True)
                    )
                except psutil.Error:
                    pass  # Missing observations keep the full reservation.
            uncommitted += max(0, self._limits["memory_bytes"] - rss)
        return (
            psutil.virtual_memory().available
            >= self._limits["reserve_bytes"] + uncommitted
        )

    def _request(
        self,
        command: str,
        *,
        timeout: float,
        cwd: str | None,
        env: dict[str, str] | None,
    ) -> bytes:
        return (
            json.dumps(
                dict(
                    command=command,
                    timeout=timeout,
                    cwd=cwd,
                    env=env,
                    limits=self._limits,
                )
            )
            + "\n"
        ).encode()

    async def _run(
        self,
        command: str,
        *,
        timeout: float,
        cwd: str | None,
        env: dict[str, str] | None,
        reservation: object | None = None,
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
            if reservation is not None:
                try:
                    self._processes[reservation] = psutil.Process(proc.pid)
                except psutil.Error:
                    pass  # Keep the full reservation if observation is unavailable.
            assert proc.stdin is not None
            proc.stdin.write(self._request(command, timeout=timeout, cwd=cwd, env=env))
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
                    await self._wait_retained(creation)
                    proc = creation.result()
                except OSError:
                    pass
            raise
        except (OSError, ValueError, TypeError, TimeoutError) as exc:
            return ExecResult(termination_reason="supervisor_error", error=str(exc))
        finally:
            try:
                if proc is not None:
                    cleanup = asyncio.create_task(self._close_supervisor(proc))
                    interrupted = await self._wait_retained(cleanup)
                    cleanup.result()
                    if interrupted:
                        raise asyncio.CancelledError
            finally:
                if response is not None:
                    response.cancel()
                    await asyncio.gather(response, return_exceptions=True)

    @staticmethod
    async def _wait_retained(task: asyncio.Task[Any]) -> bool:
        """Repeated cancellation must not abandon spawn or bounded process teardown."""
        interrupted = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                interrupted = True
        return interrupted

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
