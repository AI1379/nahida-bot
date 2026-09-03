"""External script execution for scheduled jobs."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from time import monotonic

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

    @property
    def succeeded(self) -> bool:
        return self.return_code == 0 and not self.timed_out and not self.spawn_error


async def execute_script(job: CronJob) -> ScriptExecutionResult:
    """Run one external script attempt and capture bounded diagnostics."""
    started_at = monotonic()
    try:
        proc = await asyncio.create_subprocess_shell(
            job.script_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=job.script_working_dir or None,
        )
    except Exception as exc:
        return ScriptExecutionResult(
            command=job.script_command,
            working_dir=job.script_working_dir,
            return_code=None,
            duration_seconds=monotonic() - started_at,
            spawn_error=f"{type(exc).__name__}: {exc}",
        )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=job.script_timeout_seconds,
        )
    except asyncio.CancelledError:
        proc.kill()
        await proc.wait()
        raise
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return ScriptExecutionResult(
            command=job.script_command,
            working_dir=job.script_working_dir,
            return_code=proc.returncode,
            duration_seconds=monotonic() - started_at,
            timed_out=True,
        )
    except Exception as exc:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        return ScriptExecutionResult(
            command=job.script_command,
            working_dir=job.script_working_dir,
            return_code=proc.returncode,
            duration_seconds=monotonic() - started_at,
            spawn_error=f"{type(exc).__name__}: {exc}",
        )

    return ScriptExecutionResult(
        command=job.script_command,
        working_dir=job.script_working_dir,
        return_code=proc.returncode,
        stdout=_decode_output(stdout_bytes),
        stderr=_decode_output(stderr_bytes),
        duration_seconds=monotonic() - started_at,
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
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
    return (
        "The scheduled task first attempted an external script, but the "
        "script did not complete successfully. Use the original task request "
        "and the execution context below to understand what happened and "
        "complete the task if possible. The script may have already produced "
        "partial external side effects, including sending a message.\n\n"
        '<script_execution_context trust="untrusted">\n'
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
        "</script_execution_context>"
    )


def _decode_output(value: bytes) -> str:
    text = value.decode("utf-8", errors="replace")
    if len(text) <= _OUTPUT_LIMIT_CHARS:
        return text
    return text[:_OUTPUT_LIMIT_CHARS] + "\n... (output truncated)"
