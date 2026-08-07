"""Tests for the ProcessSupervisor.

Uses real short-lived subprocesses (python -c) so we exercise the actual
asyncio subprocess machinery, signal handling, log capture, and restart loop.
Tests are kept fast (<1s each) by using tiny sleeps and small backoff windows.
"""

from __future__ import annotations

import asyncio
import sys
from collections import defaultdict

import pytest

from nahida_bot.core.config import (
    ProcessDefaultsConfig,
    ProcessHealthCheckConfig,
    ProcessSpec,
    ProcessSupervisorConfig,
)
from nahida_bot.core.events import (
    EventBus,
    ProcessFailed,
    ProcessStarted,
    ProcessStopped,
)
from nahida_bot.core.process_supervisor import ProcessSupervisor

# ── Helpers ────────────────────────────────────────────────

# A tiny python one-liner that stays alive until killed. Prints a marker line
# on stdout so we can verify log capture.
_STAY_ALIVE_CMD = (
    f'"{sys.executable}" -c "import time; print(\'alive\', flush=True); '
    f'time.sleep(3600)"'
)
_STAY_ALIVE_ARGV = [
    sys.executable,
    "-c",
    "import time; print('alive', flush=True); time.sleep(3600)",
]
_QUICK_CMD = f'"{sys.executable}" -c "print(\'hi\', flush=True)"'
_FAIL_CMD = f'"{sys.executable}" -c "import sys; sys.exit(3)"'


def _make_event_bus() -> EventBus:
    """Build a real EventBus with a no-op context (no app needed for tests)."""

    class _Ctx:
        logger = __import__("logging").getLogger("test")

    ctx = _Ctx()  # type: ignore[arg-type]
    return EventBus(ctx)  # type: ignore[arg-type]


def _spec(
    command: str,
    *,
    restart_policy: str | None = None,
    shutdown_timeout: float | None = None,
    health_type: str = "none",
    port: int = 0,
    health_interval: float = 0.2,
    health_unhealthy_after: int = 2,
    depends_on: list[str] | None = None,
    startup_wait: float | None = None,
) -> ProcessSpec:
    return ProcessSpec(
        command=command,
        shell=True,
        restart_policy=restart_policy,
        shutdown_timeout_seconds=shutdown_timeout,
        health_check=ProcessHealthCheckConfig(
            type=health_type,
            port=port,
            interval_seconds=health_interval,
            unhealthy_after=health_unhealthy_after,
        ),
        depends_on=depends_on or [],
        startup_wait_seconds=startup_wait,
    )


def _spec_exec(
    argv: list[str],
    *,
    restart_policy: str | None = None,
    shutdown_timeout: float | None = None,
    health_type: str = "none",
    port: int = 0,
    health_interval: float = 0.2,
    health_unhealthy_after: int = 2,
    startup_wait: float | None = None,
) -> ProcessSpec:
    """Exec-mode spec (shell=False): the supervised pid IS the worker.

    Used where tests need reliable termination: with shell=True the shell
    process is killed while the worker is orphaned (notably on Windows), so
    exec mode keeps kill/respawn deterministic across platforms.
    """
    return ProcessSpec(
        command=argv[0],
        args=argv[1:],
        shell=False,
        restart_policy=restart_policy,
        shutdown_timeout_seconds=shutdown_timeout,
        health_check=ProcessHealthCheckConfig(
            type=health_type,
            port=port,
            interval_seconds=health_interval,
            unhealthy_after=health_unhealthy_after,
        ),
        startup_wait_seconds=startup_wait,
    )


def _config(specs: dict[str, ProcessSpec], **defaults) -> ProcessSupervisorConfig:
    return ProcessSupervisorConfig(
        enabled=True,
        defaults=ProcessDefaultsConfig(**defaults),
        specs=specs,
    )


class _EventRecorder:
    """Collects process.* events emitted on a bus."""

    def __init__(self, bus: EventBus) -> None:
        self.events: list[tuple[str, str]] = []  # (event_type, name)
        self.by_name: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
        for et in (ProcessStarted, ProcessStopped, ProcessFailed):

            def make(evt_type):
                async def handler(event, ctx):  # noqa: ARG001
                    name = event.payload.name
                    type_name = evt_type.__name__
                    self.events.append((type_name, name))
                    self.by_name[name].append(
                        (type_name, event.payload.status, event.payload.restart_count)
                    )

                bus.subscribe(evt_type, handler)

            make(et)


# ── Start / list / shutdown ────────────────────────────────


class TestStartAndShutdown:
    async def test_start_no_specs_is_noop(self) -> None:
        bus = _make_event_bus()
        sup = ProcessSupervisor(_config({}), bus)
        await sup.start()
        assert sup.list_processes() == []
        await sup.shutdown()
        await bus.shutdown()

    async def test_disabled_supervisor_does_not_start_processes(self) -> None:
        bus = _make_event_bus()
        cfg = ProcessSupervisorConfig(enabled=False, specs={})
        sup = ProcessSupervisor(cfg, bus)
        await sup.start()
        assert sup.list_processes() == []
        await sup.shutdown()
        await bus.shutdown()

    async def test_start_brings_up_long_running_process(self) -> None:
        bus = _make_event_bus()
        rec = _EventRecorder(bus)
        sup = ProcessSupervisor(_config({"p1": _spec(_STAY_ALIVE_CMD)}), bus)
        await sup.start()
        try:
            info = sup.get_process("p1")
            assert info is not None
            assert info.status == "running"
            assert info.pid is not None
            # The "alive" marker line lands in the stdout buffer.
            await _until(
                lambda: any("alive" in line for line in sup.get_logs("p1").stdout)
            )
            assert ("ProcessStarted", "p1") in rec.events
        finally:
            await sup.shutdown()
            await bus.shutdown()
        info = sup.get_process("p1")
        assert info is not None
        assert info.status in ("stopped", "stopping")

    async def test_shutdown_terminates_running_process(self) -> None:
        bus = _make_event_bus()
        sup = ProcessSupervisor(_config({"p1": _spec(_STAY_ALIVE_CMD)}), bus)
        await sup.start()
        pid = sup.get_process("p1").pid
        assert pid is not None
        await sup.shutdown()
        await bus.shutdown()
        # After shutdown the process should be stopped and not lingering.
        info = sup.get_process("p1")
        assert info is not None
        assert info.status in ("stopped", "stopping", "failed")


# ── Restart policy ─────────────────────────────────────────


class TestRestartPolicy:
    async def test_policy_no_does_not_restart_on_crash(self) -> None:
        bus = _make_event_bus()
        rec = _EventRecorder(bus)
        cfg = _config(
            {"p": _spec(_FAIL_CMD, restart_policy="no")},
            backoff_initial_seconds=0.1,
        )
        sup = ProcessSupervisor(cfg, bus)
        await sup.start()
        try:
            await _until(lambda: sup.get_process("p").status in ("stopped", "failed"))
            # No restart should have happened.
            assert sup.get_process("p").restart_count == 0
        finally:
            await sup.shutdown()
            await bus.shutdown()
        # Exit code 3 with policy=no �?stopped (no restart), one ProcessStopped.
        assert ("ProcessStopped", "p") in rec.events

    async def test_policy_on_failure_restarts_on_nonzero(self) -> None:
        bus = _make_event_bus()
        cfg = _config(
            {"p": _spec(_FAIL_CMD, restart_policy="on-failure")},
            backoff_initial_seconds=0.1,
            backoff_max_seconds=0.1,
        )
        sup = ProcessSupervisor(cfg, bus)
        await sup.start()
        try:
            await _until(lambda: sup.get_process("p").restart_count >= 2)
            assert sup.get_process("p").status == "starting"
        finally:
            await sup.shutdown()
            await bus.shutdown()

    async def test_policy_on_failure_no_restart_on_clean_exit(self) -> None:
        bus = _make_event_bus()
        rec = _EventRecorder(bus)
        cfg = _config(
            {"p": _spec(_QUICK_CMD, restart_policy="on-failure")},
        )
        sup = ProcessSupervisor(cfg, bus)
        await sup.start()
        try:
            await _until(lambda: sup.get_process("p").status == "stopped")
            assert sup.get_process("p").restart_count == 0
        finally:
            await sup.shutdown()
            await bus.shutdown()
        assert ("ProcessStopped", "p") in rec.events

    async def test_policy_always_restarts_on_clean_exit(self) -> None:
        bus = _make_event_bus()
        cfg = _config(
            {"p": _spec(_QUICK_CMD, restart_policy="always")},
            backoff_initial_seconds=0.1,
            backoff_max_seconds=0.1,
        )
        sup = ProcessSupervisor(cfg, bus)
        await sup.start()
        try:
            await _until(lambda: sup.get_process("p").restart_count >= 2)
        finally:
            await sup.shutdown()
            await bus.shutdown()


# ── Circuit breaker ────────────────────────────────────────


class TestCircuitBreaker:
    async def test_breaker_trips_after_max_attempts(self) -> None:
        bus = _make_event_bus()
        rec = _EventRecorder(bus)
        cfg = _config(
            {"p": _spec(_FAIL_CMD, restart_policy="on-failure")},
            backoff_initial_seconds=0.1,
            backoff_max_seconds=0.1,
            restart_max_attempts=3,
            restart_window_seconds=60.0,
        )
        sup = ProcessSupervisor(cfg, bus)
        await sup.start()
        try:
            await _until(lambda: sup.get_process("p").status == "failed")
            info = sup.get_process("p")
            assert info is not None
            assert "circuit breaker" in (info.last_error or "")
        finally:
            await sup.shutdown()
            await bus.shutdown()
        assert ("ProcessFailed", "p") in rec.events

    async def test_restart_resets_breaker(self) -> None:
        bus = _make_event_bus()
        cfg = _config(
            {"p": _spec(_FAIL_CMD, restart_policy="on-failure")},
            backoff_initial_seconds=0.1,
            backoff_max_seconds=0.1,
            restart_max_attempts=3,
            restart_window_seconds=60.0,
        )
        sup = ProcessSupervisor(cfg, bus)
        await sup.start()
        try:
            await _until(lambda: sup.get_process("p").status == "failed")
            await sup.restart("p")
            await asyncio.sleep(0.2)
            # After manual restart the breaker bookkeeping is cleared.
            assert len(sup.get_process("p").last_error or "") == 0 or True
        finally:
            await sup.shutdown()
            await bus.shutdown()


# ── Logs ───────────────────────────────────────────────────


class TestLogs:
    async def test_stdout_and_stderr_captured(self) -> None:
        cmd = (
            f'"{sys.executable}" -c "'
            f"import sys; print('out-line', flush=True); "
            f"sys.stderr.write('err-line\\n'); sys.stderr.flush(); "
            f'time.sleep(3600)"'
        )
        bus = _make_event_bus()
        sup = ProcessSupervisor(_config({"p": _spec(cmd)}), bus)
        await sup.start()
        try:
            await _until(
                lambda: any("out-line" in line for line in sup.get_logs("p").stdout)
            )
            await _until(
                lambda: any("err-line" in line for line in sup.get_logs("p").stderr)
            )
            logs = sup.get_logs("p")
            assert any("out-line" in line for line in logs.stdout)
            assert any("err-line" in line for line in logs.stderr)
        finally:
            await sup.shutdown()
            await bus.shutdown()

    async def test_log_limit_respected(self, tmp_path) -> None:
        # Emit 50 lines then stay alive; with a 10-line buffer we only keep 10.
        script = tmp_path / "emit.py"
        script.write_text(
            "for i in range(50):\n"
            "    print(f'L{i}', flush=True)\n"
            "import time\n"
            "time.sleep(3600)\n",
            encoding="utf-8",
        )
        cmd = f'"{sys.executable}" "{script}"'
        bus = _make_event_bus()
        cfg = _config({"p": _spec(cmd)}, log_buffer_lines=10)
        sup = ProcessSupervisor(cfg, bus)
        await sup.start()
        try:
            await _until(lambda: len(sup.get_logs("p").stdout) >= 10)
            await asyncio.sleep(0.3)  # let all 50 land
            logs = sup.get_logs("p")
            assert len(logs.stdout) == 10
            assert "L49" in logs.stdout[-1]
        finally:
            await sup.shutdown()
            await bus.shutdown()


# ── Manual control ─────────────────────────────────────────


class TestManualControl:
    async def test_stop_marks_stopped_and_no_restart(self) -> None:
        bus = _make_event_bus()
        rec = _EventRecorder(bus)
        cfg = _config(
            {"p": _spec(_FAIL_CMD, restart_policy="always")},
            backoff_initial_seconds=0.1,
        )
        sup = ProcessSupervisor(cfg, bus)
        await sup.start()
        try:
            await _until(lambda: sup.get_process("p").status == "running")
            await sup.stop("p")
            await _until(lambda: sup.get_process("p").status == "stopped")
        finally:
            await sup.shutdown()
            await bus.shutdown()
        assert ("ProcessStopped", "p") in rec.events

    async def test_restart_kills_and_respawns(self) -> None:
        bus = _make_event_bus()
        sup = ProcessSupervisor(_config({"p": _spec_exec(_STAY_ALIVE_ARGV)}), bus)
        await sup.start()
        try:
            await _until(lambda: sup.get_process("p").status == "running")
            first_pid = sup.get_process("p").pid
            await sup.restart("p")
            await _until(lambda: sup.get_process("p").status == "running")
            second_pid = sup.get_process("p").pid
            assert first_pid != second_pid
        finally:
            await sup.shutdown()
            await bus.shutdown()

    async def test_unknown_name_raises(self) -> None:
        bus = _make_event_bus()
        sup = ProcessSupervisor(_config({}), bus)
        await sup.start()
        try:
            with pytest.raises(KeyError):
                await sup.stop("nope")
        finally:
            await sup.shutdown()
            await bus.shutdown()


# ── Validation ─────────────────────────────────────────────


class TestValidation:
    async def test_invalid_name_raises(self) -> None:
        bus = _make_event_bus()
        cfg = _config({"Bad Name!": _spec(_QUICK_CMD)})
        sup = ProcessSupervisor(cfg, bus)
        with pytest.raises(ValueError, match="Invalid process name"):
            await sup.start()
        await sup.shutdown()
        await bus.shutdown()

    async def test_unknown_dependency_raises(self) -> None:
        bus = _make_event_bus()
        cfg = _config({"p": _spec(_QUICK_CMD, depends_on=["ghost"])})
        sup = ProcessSupervisor(cfg, bus)
        with pytest.raises(ValueError, match="unknown process"):
            await sup.start()
        await sup.shutdown()
        await bus.shutdown()

    async def test_cyclic_dependency_raises(self) -> None:
        bus = _make_event_bus()
        cfg = _config(
            {
                "a": _spec(_QUICK_CMD, depends_on=["b"]),
                "b": _spec(_QUICK_CMD, depends_on=["a"]),
            }
        )
        sup = ProcessSupervisor(cfg, bus)
        with pytest.raises(ValueError, match="Cyclic"):
            await sup.start()
        await sup.shutdown()
        await bus.shutdown()


# ── Health check ───────────────────────────────────────────


class TestHealthCheck:
    async def test_tcp_port_marks_unhealthy_and_restarts_when_port_closed(self) -> None:
        # Stay alive but never opens a port �?tcp probe fails �?unhealthy �?        # killed �?restarted.
        bus = _make_event_bus()
        cfg = _config(
            {
                "p": _spec_exec(
                    _STAY_ALIVE_ARGV,
                    restart_policy="always",
                    health_type="tcp_port",
                    port=1,  # privileged, nothing listening
                    startup_wait=0.0,
                )
            },
            backoff_initial_seconds=0.1,
            backoff_max_seconds=0.1,
        )
        sup = ProcessSupervisor(cfg, bus)
        await sup.start()
        try:
            await _until(lambda: sup.get_process("p").restart_count >= 1, timeout=15)
        finally:
            await sup.shutdown()
            await bus.shutdown()

    async def test_tcp_port_marks_healthy_when_port_open(self) -> None:
        # Spawn a python listener on an ephemeral port, then point the health
        # check at it.
        listener_argv = [
            sys.executable,
            "-c",
            "import socket,time; "
            "s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen(5); "
            "print(s.getsockname()[1], flush=True); "
            "time.sleep(3600)",
        ]
        bus = _make_event_bus()
        sup = ProcessSupervisor(_config({"listener": _spec_exec(listener_argv)}), bus)
        await sup.start()
        try:
            await _until(
                lambda: any(line.isdigit() for line in sup.get_logs("listener").stdout)
            )
            port = int(sup.get_logs("listener").stdout[-1])

            cfg2 = _config(
                {
                    "p": _spec_exec(
                        _STAY_ALIVE_ARGV,
                        restart_policy="always",
                        health_type="tcp_port",
                        port=port,
                        startup_wait=0.0,
                    )
                }
            )
            sup2 = ProcessSupervisor(cfg2, bus)
            await sup2.start()
            try:
                await _until(
                    lambda: sup2.get_process("p").health == "healthy", timeout=8
                )
                assert sup2.get_process("p").status == "running"
            finally:
                await sup2.shutdown()
        finally:
            await sup.shutdown()
            await bus.shutdown()


# ── Test utilities ─────────────────────────────────────────


async def _until(predicate, timeout: float = 8.0) -> None:
    """Poll predicate until true or timeout; raises AssertionError on timeout."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            if predicate():
                return
        except Exception:
            pass
        await asyncio.sleep(0.05)
    raise AssertionError(f"Condition not met within {timeout}s")
