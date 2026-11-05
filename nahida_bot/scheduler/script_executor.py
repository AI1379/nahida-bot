"""External script execution for scheduled jobs."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from time import monotonic

from nahida_bot.core.exec_env import build_exec_env
from nahida_bot.core.exec_runner import ExecRunner
from nahida_bot.scheduler.models import CronJob

_OUTPUT_LIMIT_CHARS = 12000


@dataclass(slots=True, frozen=True)
class ScriptExecutionResult:
    """Captured result of one external CRON script attempt."""

    command: str
    working_dir: str
    return_code: int | None
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    timed_out: bool = False
    spawn_error: str = ""
    termination_reason: str = "completed"
    peak_rss_bytes: int = 0
    error: str = ""
    truncated: bool = False
    started: bool | None = None
    queue_wait_seconds: float = 0.0
    execution_timeout_seconds: float | None = None

    @property
    def admission_deferred(self) -> bool:
        return self.termination_reason in {"busy", "queue_full", "queue_timeout"} or (
            self.termination_reason == "host_memory_pressure" and self.started is False
        )

    @property
    def succeeded(self) -> bool:
        return (
            self.return_code == 0
            and not self.timed_out
            and not self.spawn_error
            and self.termination_reason == "completed"
        )


async def execute_script(
    job: CronJob,
    *,
    exec_runner: ExecRunner | None = None,
) -> ScriptExecutionResult:
    """Use the same application-owned runner and budget as the exec tool."""
    started_at = monotonic()
    runner = exec_runner if exec_runner is not None else ExecRunner()
    try:
        result = await runner.run(
            job.script_command,
            timeout=job.script_timeout_seconds,
            cwd=job.script_working_dir or None,
            env=build_exec_env(),
            owner=job.sender_account_key or job.session_key,
            service_class="background",
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return ScriptExecutionResult(
            command=job.script_command,
            working_dir=job.script_working_dir,
            return_code=None,
            duration_seconds=monotonic() - started_at,
            spawn_error=f"{type(exc).__name__}: {exc}",
            termination_reason="spawn_error",
        )
    return ScriptExecutionResult(
        command=job.script_command,
        working_dir=job.script_working_dir,
        return_code=result.returncode,
        stdout=_truncate_output(result.stdout),
        stderr=_truncate_output(result.stderr),
        duration_seconds=monotonic() - started_at,
        timed_out=result.termination_reason == "timeout",
        spawn_error=result.error if result.termination_reason == "spawn_error" else "",
        termination_reason=result.termination_reason,
        peak_rss_bytes=result.peak_rss_bytes,
        error=result.error,
        started=result.started,
        queue_wait_seconds=result.queue_wait_seconds,
        execution_timeout_seconds=result.execution_timeout_seconds,
        truncated=result.truncated
        or max(len(result.stdout), len(result.stderr)) > _OUTPUT_LIMIT_CHARS,
    )


def render_fallback_context(job: CronJob, result: ScriptExecutionResult) -> str:
    """Render a failed script attempt as ephemeral Agent context."""
    payload = {
        "job_id": job.job_id,
        "command": result.command,
        "working_dir": result.working_dir,
        "return_code": result.return_code,
        "timed_out": result.timed_out,
        "duration_seconds": round(result.duration_seconds, 3),
        "spawn_error": result.spawn_error,
        "termination_reason": result.termination_reason,
        "started": result.started,
        "queue_wait_seconds": result.queue_wait_seconds,
        "execution_timeout_seconds": result.execution_timeout_seconds,
        "peak_rss_bytes": result.peak_rss_bytes,
        "error": result.error,
        "truncated": result.truncated,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
    return (
        "The scheduled task first attempted an external script, but the "
        "script did not complete successfully. Use the original task request "
        "and the execution context below to understand what happened and "
        "complete the task if possible. Resource-limit failures require reducing the "
        "workload; do not retry the same oversized command unchanged. "
        "The script may have already produced "
        "partial external side effects, including sending a message.\n\n"
        '<script_execution_context trust="untrusted">\n'
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
        "</script_execution_context>"
    )


def _truncate_output(text: str) -> str:
    if len(text) <= _OUTPUT_LIMIT_CHARS:
        return text
    return text[:_OUTPUT_LIMIT_CHARS] + "\n... (output truncated)"
