"""Supervised long-lived OS subprocess manager.

A centralized supervisor for sidecar processes declared in ``config.yaml``
(SSH tunnels, frpc, cloudflared, TTS backends, etc.). It is the OS-process
counterpart to :class:`~nahida_bot.core.tasks.TaskManager`:

- Every process has a unique name and an owner (``"core.config"`` or a
  plugin id).
- It is started deterministically before channels connect and stopped after
  they disconnect (see Application lifecycle wiring).
- Crash recovery uses an exponential backoff with a sliding-window circuit
  breaker to prevent restart storms.
- Optional ``tcp_port`` health probes mark a process unhealthy and force a
  restart after ``unhealthy_after`` consecutive failures.
- stdout/stderr are kept in per-process ring buffers for the WebUI.

See ``docs/design/process-supervisor.md`` for the full design.
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
import sys
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

import structlog

from nahida_bot.core.config import (
    ProcessDefaultsConfig,
    ProcessSpec,
    ProcessSupervisorConfig,
)
from nahida_bot.core.events import (
    EventBus,
    ProcessFailed,
    ProcessPayload,
    ProcessStarted,
    ProcessStopped,
)

if TYPE_CHECKING:
    from asyncio import StreamReader
    from asyncio.subprocess import Process

logger = structlog.get_logger(__name__)

ProcessStatus = Literal[
    "pending",
    "starting",
    "running",
    "unhealthy",
    "stopping",
    "stopped",
    "failed",
    "disabled",
]
HealthState = Literal["unknown", "healthy", "unhealthy"]

# Environment variables always inherited from the bot process. Sidecars do NOT
# receive the full parent environment — only this whitelist plus user-declared
# ``env`` entries — so secrets in the bot environment are not silently leaked.
_ENV_WHITELIST_ALWAYS = frozenset(
    {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "HOME", "USER", "TMP", "TEMP"}
)
_ENV_WHITELIST_WINDOWS = frozenset({"SYSTEMROOT", "WINDIR", "APPDATA", "USERPROFILE"})

# Validation pattern for process names (also enforced for plugin-contributed).
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")

# Reason codes for terminal exits (used in events/logs, not in ProcessInfo).
# Grace period granted during start() to let each process reach a ready or
# terminal state before its dependents are launched.
_READY_GRACE_SECONDS = 5.0


@dataclass(slots=True, frozen=True)
class ProcessInfo:
    """Immutable snapshot of a managed process's metadata and current state."""

    name: str
    owner: str
    status: ProcessStatus
    pid: int | None
    restart_count: int
    exit_code: int | None
    started_at: datetime | None
    last_error: str | None = None
    health: HealthState = "unknown"
    restart_policy: str = "on-failure"
    command: str = ""


@dataclass(slots=True, frozen=True)
class ProcessLogs:
    """Tail of a process's captured stdout/stderr."""

    name: str
    stdout: list[str]
    stderr: list[str]
    truncated: bool = False


@dataclass(slots=True, frozen=True)
class _ResolvedSpec:
    """A :class:`ProcessSpec` merged with supervisor-level defaults."""

    name: str
    command: str
    args: list[str]
    shell: bool
    env: dict[str, str]
    working_dir: str | None
    restart_policy: str
    health_type: str
    health_host: str
    health_port: int
    health_interval: float
    health_timeout: float
    health_unhealthy_after: int
    health_start_period: float
    depends_on: list[str]
    shutdown_timeout: float
    startup_wait: float
    backoff_initial: float
    backoff_max: float
    backoff_factor: float
    restart_max_attempts: int
    restart_window: float
    log_buffer_lines: int


@dataclass(slots=True)
class _ManagedProcess:
    """Mutable tracking record for one supervised process."""

    name: str
    owner: str
    resolved: _ResolvedSpec
    status: ProcessStatus = "pending"
    proc: Process | None = None
    pid: int | None = None
    restart_count: int = 0
    exit_code: int | None = None
    started_at: datetime | None = None
    last_error: str | None = None
    health: HealthState = "unknown"
    # Restart bookkeeping
    restart_timestamps: deque[float] = field(default_factory=deque)
    current_backoff: float = 0.0
    # Control signals
    stop_requested: asyncio.Event = field(default_factory=asyncio.Event)
    restart_requested: asyncio.Event = field(default_factory=asyncio.Event)
    running_emitted: bool = False
    generation: int = 0  # bumped on every (re)spawn; used to await restarts
    # Log ring buffers (line-by-line, no trailing newline)
    stdout_buf: deque[str] = field(default_factory=deque)
    stderr_buf: deque[str] = field(default_factory=deque)
    # Background tasks owned by this process
    tasks: set[asyncio.Task[Any]] = field(default_factory=set)


def _validate_name(name: str) -> None:
    if not _NAME_RE.match(name):
        raise ValueError(
            f"Invalid process name {name!r}: must match {_NAME_RE.pattern}"
        )


def _validate_dependencies(specs: Mapping[str, _ResolvedSpec]) -> None:
    """Reject depends_on entries that are unknown or form a cycle (DFS)."""
    declared = set(specs)
    for name, spec in specs.items():
        for dep in spec.depends_on:
            if dep == name:
                raise ValueError(f"Process {name!r} depends on itself")
            if dep not in declared:
                raise ValueError(f"Process {name!r} depends on unknown process {dep!r}")
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in specs}

    def visit(node: str) -> None:
        color[node] = GRAY
        for dep in specs[node].depends_on:
            if color[dep] == GRAY:
                raise ValueError(
                    f"Cyclic depends_on detected involving {node!r} -> {dep!r}"
                )
            if color[dep] == WHITE:
                visit(dep)
        color[node] = BLACK

    for n in specs:
        if color[n] == WHITE:
            visit(n)


def _resolve_spec(
    name: str, spec: ProcessSpec, defaults: ProcessDefaultsConfig
) -> _ResolvedSpec:
    hc = spec.health_check
    return _ResolvedSpec(
        name=name,
        command=spec.command,
        args=list(spec.args),
        shell=spec.shell,
        env=dict(spec.env),
        working_dir=spec.working_dir,
        restart_policy=spec.restart_policy or defaults.restart_policy,
        health_type=hc.type,
        health_host=hc.host,
        health_port=hc.port,
        health_interval=hc.interval_seconds,
        health_timeout=hc.timeout_seconds,
        health_unhealthy_after=hc.unhealthy_after,
        health_start_period=hc.start_period_seconds,
        depends_on=list(spec.depends_on),
        shutdown_timeout=spec.shutdown_timeout_seconds
        or defaults.shutdown_timeout_seconds,
        startup_wait=spec.startup_wait_seconds
        if spec.startup_wait_seconds is not None
        else defaults.startup_wait_seconds,
        backoff_initial=defaults.backoff_initial_seconds,
        backoff_max=defaults.backoff_max_seconds,
        backoff_factor=defaults.backoff_factor,
        restart_max_attempts=defaults.restart_max_attempts,
        restart_window=defaults.restart_window_seconds,
        log_buffer_lines=defaults.log_buffer_lines,
    )


def _build_env(user_env: Mapping[str, str]) -> dict[str, str]:
    """Whitelisted parent env + user-declared env (user wins)."""
    env: dict[str, str] = {}
    whitelist = _ENV_WHITELIST_ALWAYS | (
        _ENV_WHITELIST_WINDOWS if sys.platform == "win32" else frozenset()
    )
    for key in whitelist:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    env.update(user_env)
    return env


class ProcessSupervisor:
    """Centralized supervisor for long-lived OS subprocesses.

    Lifecycle:

    - :meth:`start` reads config, resolves specs, validates names + dependencies,
      then brings processes up in dependency order and enters supervision.
    - :meth:`shutdown` SIGTERMs every process, waits per-spec, SIGKILLs
      stragglers, and cancels all supervision/health/reader tasks.
    """

    def __init__(
        self,
        config: ProcessSupervisorConfig,
        event_bus: EventBus,
    ) -> None:
        self._config = config
        self._event_bus = event_bus
        self._logger = structlog.get_logger("process_supervisor")
        self._managed: dict[str, _ManagedProcess] = {}
        self._resolved: dict[str, _ResolvedSpec] = {}
        self._global_started = False
        self._shutting_down = False

    # ── Lifecycle ───────────────────────────────────────

    async def start(self) -> None:
        """Resolve specs, validate, and bring up all enabled processes."""
        if self._global_started:
            self._logger.warning("process_supervisor.already_started")
            return
        self._global_started = True

        if not self._config.enabled:
            self._logger.info("process_supervisor.disabled_by_config")
            return

        # Resolve + validate before spawning anything.
        for name, spec in self._config.specs.items():
            _validate_name(name)
            self._resolved[name] = _resolve_spec(name, spec, self._config.defaults)
        _validate_dependencies(self._resolved)

        if not self._resolved:
            self._logger.info("process_supervisor.no_specs")
            return

        # Bring processes up in dependency order.
        order = self._topological_order()
        for name in order:
            await self._bring_up(name)
            # Wait for this process to reach a ready/terminal state before
            # starting dependents, so that e.g. an SSH tunnel is actually up
            # (or definitively failed) before the channel that needs it.
            spec = self._resolved[name]
            await self._wait_ready(
                name, timeout=max(spec.startup_wait, _READY_GRACE_SECONDS)
            )

        self._logger.info(
            "process_supervisor.started",
            count=len(self._resolved),
            order=order,
        )

    async def shutdown(self, timeout: float = 10.0) -> None:
        """Stop every process and cancel all supervision tasks."""
        if not self._global_started:
            return
        self._shutting_down = True
        self._logger.info("process_supervisor.shutting_down")

        # Request stop on every managed process; the supervise loops observe
        # the flag and exit without restarting.
        for managed in self._managed.values():
            managed.stop_requested.set()

        # SIGTERM each live subprocess, then await its supervise task.
        stop_tasks: list[asyncio.Task[Any]] = []
        for managed in self._managed.values():
            stop_tasks.append(
                asyncio.create_task(
                    self._terminate(managed, managed.resolved.shutdown_timeout),
                    name=f"ps:stop:{managed.name}",
                )
            )
        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)

        # Cancel any lingering background tasks.
        all_tasks: list[asyncio.Task[Any]] = []
        for managed in self._managed.values():
            all_tasks.extend(managed.tasks)
        for task in all_tasks:
            if not task.done():
                task.cancel()
        if all_tasks:
            await asyncio.gather(*all_tasks, return_exceptions=True)

        self._logger.info("process_supervisor.shutdown_complete")

    # ── Runtime control ─────────────────────────────────

    async def restart(self, name: str) -> ProcessInfo:
        """Restart a process immediately, resetting its circuit breaker.

        Blocks until the new incarnation has spawned and reached a ready or
        terminal state, so callers observe a fresh pid on return.
        """
        managed = self._require(name)
        managed.restart_timestamps.clear()
        managed.current_backoff = 0.0
        target_generation = managed.generation + 1
        managed.restart_requested.set()
        await self._kill_current(managed)
        # The supervise loop observes the exit and respawns without backoff.
        await self._wait_generation(managed, target_generation)
        return self.get_process(name)  # type: ignore[return-value]

    async def _wait_generation(
        self, managed: _ManagedProcess, target: int, timeout: float = 15.0
    ) -> None:
        """Wait until a new incarnation (generation >= target) settles."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if managed.generation >= target and managed.status in (
                "running",
                "failed",
                "stopped",
            ):
                return
            await asyncio.sleep(0.05)

    async def stop(self, name: str) -> ProcessInfo:
        """Stop a process and keep it stopped (no auto-restart)."""
        managed = self._require(name)
        managed.stop_requested.set()
        await self._kill_current(managed)
        return self.get_process(name)  # type: ignore[return-value]

    async def start_one(self, name: str) -> ProcessInfo:
        """Start a single currently-stopped process.

        Re-registers the process (fresh state, fresh buffers) and launches a
        new supervision loop. No-op if it is already running.
        """
        existing = self._managed.get(name)
        if existing is not None and existing.status in ("running", "starting"):
            return self.get_process(name)  # type: ignore[return-value]
        await self._bring_up(name)
        spec = self._resolved[name]
        await self._wait_ready(
            name, timeout=max(spec.startup_wait, _READY_GRACE_SECONDS)
        )
        return self.get_process(name)  # type: ignore[return-value]

    # ── Query ───────────────────────────────────────────

    def list_processes(self) -> list[ProcessInfo]:
        return [self._to_info(m) for m in self._managed.values()]

    def get_process(self, name: str) -> ProcessInfo | None:
        managed = self._managed.get(name)
        return self._to_info(managed) if managed is not None else None

    def get_logs(
        self, name: str, *, stream: str = "both", limit: int = 200
    ) -> ProcessLogs:
        managed = self._require(name)
        limit = max(0, min(limit, managed.resolved.log_buffer_lines))
        stdout: list[str] = []
        stderr: list[str] = []
        if stream in ("both", "stdout"):
            stdout = (
                list(managed.stdout_buf)[-limit:] if limit else list(managed.stdout_buf)
            )
        if stream in ("both", "stderr"):
            stderr = (
                list(managed.stderr_buf)[-limit:] if limit else list(managed.stderr_buf)
            )
        return ProcessLogs(
            name=name,
            stdout=stdout,
            stderr=stderr,
            truncated=False,
        )

    # ── Internal: bring-up ──────────────────────────────

    def _topological_order(self) -> list[str]:
        """Return spec names in dependency order (dependencies first)."""
        visited: set[str] = set()
        order: list[str] = []

        def visit(node: str) -> None:
            if node in visited:
                return
            visited.add(node)
            for dep in self._resolved[node].depends_on:
                visit(dep)
            order.append(node)

        for name in self._resolved:
            visit(name)
        return order

    async def _bring_up(self, name: str) -> None:
        """Register a managed record and launch its supervise loop."""
        resolved = self._resolved[name]
        managed = _ManagedProcess(name=name, owner="core.config", resolved=resolved)
        managed.stdout_buf = deque(maxlen=resolved.log_buffer_lines)
        managed.stderr_buf = deque(maxlen=resolved.log_buffer_lines)
        self._managed[name] = managed
        task = asyncio.create_task(
            self._supervise(managed), name=f"ps:supervise:{name}"
        )
        managed.tasks.add(task)
        self._logger.debug("process_supervisor.bring_up", name=name)

    async def _supervise(self, managed: _ManagedProcess) -> None:
        """Main supervision loop: spawn, wait, decide restart, repeat."""
        resolved = managed.resolved
        try:
            while True:
                if self._shutting_down or managed.stop_requested.is_set():
                    managed.status = "stopped"
                    self._emit(ProcessStopped, managed)
                    return

                spawned = await self._spawn(managed)
                if spawned is None:
                    # Spawn failed: treat as a crash subject to restart policy.
                    managed.exit_code = None
                    managed.last_error = "spawn failed"
                    if not self._should_restart(managed, exit_code=None):
                        managed.status = "failed"
                        self._emit(ProcessFailed, managed)
                        return
                    if self._trip_breaker_if_needed(managed):
                        managed.status = "failed"
                        self._emit(ProcessFailed, managed)
                        return
                    await self._backoff_sleep(managed)
                    continue

                managed.status = "starting"
                managed.started_at = datetime.now(UTC)
                managed.running_emitted = False

                # Start log readers + optional health loop.
                self._spawn_readers(managed)
                if resolved.health_type == "tcp_port":
                    self._spawn_health_loop(managed)

                # For health-less processes, running is immediate (after wait).
                if resolved.health_type == "none":
                    if resolved.startup_wait > 0:
                        await asyncio.sleep(resolved.startup_wait)
                    self._mark_running(managed)

                # Block until the process exits.
                await managed.proc.wait()  # type: ignore[union-attr]
                managed.exit_code = managed.proc.returncode  # type: ignore[union-attr]

                # Cancel ancillary tasks for this incarnation.
                await self._cancel_incarnations(managed)

                # Manual restart: clear flag, restart immediately, no breaker.
                if managed.restart_requested.is_set():
                    managed.restart_requested.clear()
                    self._mark_starting(managed)
                    continue

                if self._shutting_down or managed.stop_requested.is_set():
                    managed.status = "stopped"
                    self._emit(ProcessStopped, managed)
                    return

                if not self._should_restart(managed, managed.exit_code):
                    # Clean exit with policy that doesn't restart → stopped.
                    managed.status = "stopped"
                    self._emit(ProcessStopped, managed)
                    return

                if self._trip_breaker_if_needed(managed):
                    managed.status = "failed"
                    self._emit(ProcessFailed, managed)
                    return

                # Crashing — record + backoff, then loop to respawn.
                managed.status = "starting"
                self._emit(ProcessFailed, managed)
                await self._backoff_sleep(managed)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.exception(
                "process_supervisor.supervise_crashed", name=managed.name
            )
            managed.status = "failed"
            managed.last_error = "supervise loop crashed"
            self._emit(ProcessFailed, managed)

    async def _spawn(self, managed: _ManagedProcess) -> Process | None:
        """Spawn the subprocess for one incarnation. Returns None on failure."""
        resolved = managed.resolved
        env = _build_env(resolved.env)
        try:
            if resolved.shell:
                proc = await asyncio.create_subprocess_shell(
                    resolved.command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                    cwd=resolved.working_dir,
                    start_new_session=True,
                )
            else:
                argv = [resolved.command, *resolved.args]
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                    cwd=resolved.working_dir,
                    start_new_session=True,
                )
        except Exception as exc:
            self._logger.warning(
                "process_supervisor.spawn_failed",
                name=managed.name,
                error=str(exc),
            )
            managed.last_error = f"spawn failed: {exc}"
            return None
        managed.proc = proc
        managed.pid = proc.pid
        managed.generation += 1
        self._logger.info(
            "process_supervisor.spawned",
            name=managed.name,
            pid=proc.pid,
            generation=managed.generation,
        )
        return proc

    def _spawn_readers(self, managed: _ManagedProcess) -> None:
        proc = managed.proc
        if proc is None:
            return
        if proc.stdout is not None:
            t = asyncio.create_task(
                self._read_stream(proc.stdout, managed.stdout_buf),
                name=f"ps:stdout:{managed.name}",
            )
            managed.tasks.add(t)
        if proc.stderr is not None:
            t = asyncio.create_task(
                self._read_stream(proc.stderr, managed.stderr_buf),
                name=f"ps:stderr:{managed.name}",
            )
            managed.tasks.add(t)

    async def _read_stream(self, stream: StreamReader, buf: deque[str]) -> None:
        while True:
            line = await stream.readline()
            if not line:
                return
            try:
                buf.append(line.decode("utf-8", errors="replace").rstrip("\r\n"))
            except Exception:
                # Buffer may have been replaced during resize; ignore.
                return

    def _spawn_health_loop(self, managed: _ManagedProcess) -> None:
        t = asyncio.create_task(
            self._health_loop(managed), name=f"ps:health:{managed.name}"
        )
        managed.tasks.add(t)

    async def _health_loop(self, managed: _ManagedProcess) -> None:
        resolved = managed.resolved
        if resolved.health_start_period > 0:
            try:
                await asyncio.wait_for(
                    managed.stop_requested.wait(),
                    timeout=resolved.health_start_period,
                )
                return  # stop requested during start period
            except asyncio.TimeoutError:
                pass
        consecutive_failures = 0
        while True:
            if self._shutting_down or managed.stop_requested.is_set():
                return
            ok = await self._tcp_probe(
                resolved.health_host, resolved.health_port, resolved.health_timeout
            )
            if ok:
                consecutive_failures = 0
                if managed.health == "unhealthy":
                    managed.health = "healthy"
                    self._mark_running(managed)
                elif managed.health == "unknown":
                    managed.health = "healthy"
                    self._mark_running(managed)
            else:
                consecutive_failures += 1
                if consecutive_failures >= resolved.health_unhealthy_after:
                    if managed.health != "unhealthy":
                        managed.health = "unhealthy"
                        managed.status = "unhealthy"
                        self._logger.warning(
                            "process_supervisor.unhealthy",
                            name=managed.name,
                            failures=consecutive_failures,
                        )
                    # Kill so the supervise loop restarts the process.
                    await self._kill_current(managed)
                    return
            try:
                await asyncio.wait_for(
                    managed.stop_requested.wait(),
                    timeout=resolved.health_interval,
                )
                return
            except asyncio.TimeoutError:
                continue

    async def _tcp_probe(self, host: str, port: int, timeout: float) -> bool:
        try:
            fut = asyncio.open_connection(host, port)
            _, writer = await asyncio.wait_for(fut, timeout=timeout)
        except (OSError, asyncio.TimeoutError):
            return False
        try:
            writer.close()
            if hasattr(writer, "wait_closed"):
                await writer.wait_closed()
        except OSError:
            pass
        return True

    async def _wait_ready(self, name: str, timeout: float) -> None:
        """Best-effort wait for a process to reach a ready/terminal state.

        ``ready`` means healthily ``running`` (for probed processes the first
        successful probe flips health to ``healthy`` and status to ``running``
        together). Terminal states (``failed``/``stopped``) also satisfy the
        wait so a broken dependency does not block startup indefinitely.
        """
        managed = self._managed.get(name)
        if managed is None:
            return
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if managed.health == "healthy" or managed.status in (
                "running",
                "failed",
                "stopped",
            ):
                return
            await asyncio.sleep(0.05)
        self._logger.debug(
            "process_supervisor.wait_ready_timeout",
            name=name,
            timeout=timeout,
            status=managed.status,
        )

    # ── Internal: restart policy + breaker ──────────────

    def _should_restart(self, managed: _ManagedProcess, exit_code: int | None) -> bool:
        policy = managed.resolved.restart_policy
        if policy == "no":
            return False
        if policy == "always":
            return True
        # on-failure: restart only on non-zero exit.
        return exit_code is not None and exit_code != 0

    def _trip_breaker_if_needed(self, managed: _ManagedProcess) -> bool:
        """Record a restart attempt; return True if the circuit breaker trips."""
        managed.restart_count += 1
        now = asyncio.get_event_loop().time()
        managed.restart_timestamps.append(now)
        window = managed.resolved.restart_window
        while (
            managed.restart_timestamps and now - managed.restart_timestamps[0] > window
        ):
            managed.restart_timestamps.popleft()
        max_attempts = managed.resolved.restart_max_attempts
        if max_attempts > 0 and len(managed.restart_timestamps) >= max_attempts:
            managed.last_error = (
                f"circuit breaker tripped: {len(managed.restart_timestamps)} "
                f"restarts within {window:g}s"
            )
            self._logger.warning(
                "process_supervisor.breaker_tripped",
                name=managed.name,
                restarts=len(managed.restart_timestamps),
                window=window,
            )
            return True
        return False

    async def _backoff_sleep(self, managed: _ManagedProcess) -> None:
        resolved = managed.resolved
        if managed.current_backoff == 0.0:
            managed.current_backoff = resolved.backoff_initial
        delay = managed.current_backoff
        managed.current_backoff = min(
            delay * resolved.backoff_factor, resolved.backoff_max
        )
        try:
            await asyncio.wait_for(managed.stop_requested.wait(), timeout=delay)
            # stop requested during backoff
        except asyncio.TimeoutError:
            pass

    # ── Internal: process termination ───────────────────

    async def _kill_current(self, managed: _ManagedProcess) -> None:
        proc = managed.proc
        if proc is None or proc.returncode is not None:
            return
        self._signal_terminate(proc)
        try:
            await asyncio.wait_for(
                proc.wait(), timeout=managed.resolved.shutdown_timeout
            )
        except asyncio.TimeoutError:
            self._logger.warning(
                "process_supervisor.force_kill",
                name=managed.name,
                pid=proc.pid,
            )
            self._signal_kill(proc)
            try:
                await proc.wait()
            except ProcessLookupError:
                pass

    async def _terminate(self, managed: _ManagedProcess, timeout: float) -> None:
        """SIGTERM/SIGKILL one process during global shutdown."""
        proc = managed.proc
        if proc is None or proc.returncode is not None:
            managed.status = "stopped" if managed.status != "failed" else managed.status
            return
        managed.status = "stopping"
        self._signal_terminate(proc)
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            self._signal_kill(proc)
            try:
                await proc.wait()
            except ProcessLookupError:
                pass
        if managed.status != "failed":
            managed.status = "stopped"

    def _signal_terminate(self, proc: Process) -> None:
        try:
            if sys.platform == "win32":
                proc.terminate()
            else:
                # start_new_session=True makes the child a group leader, so
                # signalling the group reaches grandchildren (e.g. ssh spawned
                # by a shell) instead of orphaning them.
                os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

    def _signal_kill(self, proc: Process) -> None:
        try:
            if sys.platform == "win32":
                proc.kill()
            else:
                os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    async def _cancel_incarnations(self, managed: _ManagedProcess) -> None:
        """Cancel reader/health tasks for the just-exited incarnation."""
        pending = [t for t in managed.tasks if t is not asyncio.current_task()]
        for t in pending:
            if not t.done():
                t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        # Keep only the supervise task itself.
        current = asyncio.current_task()
        managed.tasks = {t for t in managed.tasks if t is current}

    # ── Internal: state transitions ─────────────────────

    def _mark_running(self, managed: _ManagedProcess) -> None:
        if managed.running_emitted:
            return
        managed.running_emitted = True
        managed.status = "running"
        self._emit(ProcessStarted, managed)

    def _mark_starting(self, managed: _ManagedProcess) -> None:
        managed.status = "starting"
        managed.health = "unknown"
        managed.running_emitted = False

    def _emit(
        self,
        event_type: type,
        managed: _ManagedProcess,
    ) -> None:
        payload = self._payload(managed)
        event = event_type(payload=payload, source="core.process_supervisor")
        self._logger.debug(
            "process_supervisor.event",
            name=managed.name,
            evt_type=event_type.__name__,
            status=managed.status,
            restart_count=managed.restart_count,
        )
        # Fire-and-forget; silently no-ops once the bus is closed at shutdown.
        self._event_bus.publish_nowait(event)

    def _payload(self, managed: _ManagedProcess) -> ProcessPayload:
        return ProcessPayload(
            name=managed.name,
            owner=managed.owner,
            status=managed.status,
            pid=managed.pid,
            restart_count=managed.restart_count,
            exit_code=managed.exit_code,
            error=managed.last_error or "",
        )

    def _to_info(self, managed: _ManagedProcess) -> ProcessInfo:
        return ProcessInfo(
            name=managed.name,
            owner=managed.owner,
            status=managed.status,
            pid=managed.pid,
            restart_count=managed.restart_count,
            exit_code=managed.exit_code,
            started_at=managed.started_at,
            last_error=managed.last_error,
            health=managed.health,
            restart_policy=managed.resolved.restart_policy,
            command=managed.resolved.command,
        )

    def _require(self, name: str) -> _ManagedProcess:
        managed = self._managed.get(name)
        if managed is None:
            raise KeyError(f"Process {name!r} not found")
        return managed
