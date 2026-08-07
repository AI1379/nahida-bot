"""Tests for the /api/processes WebAPI routes.

Mounts a real ProcessSupervisor behind the FastAPI app (via a lightweight
mock Application) and exercises list/detail/logs/start/stop/restart plus the
404 and 503 error paths.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from nahida_bot.core.config import (
    ProcessDefaultsConfig,
    ProcessSpec,
    ProcessSupervisorConfig,
    Settings,
    WebAPIConfigModel,
)
from nahida_bot.core.events import EventBus
from nahida_bot.core.process_supervisor import ProcessSupervisor
from nahida_bot.gateway.app import WebAPIApp

_STAY_ALIVE_ARGV = [
    sys.executable,
    "-c",
    "import time; print('alive', flush=True); time.sleep(3600)",
]


def _make_app_with_supervisor(supervisor: ProcessSupervisor | None) -> MagicMock:
    settings = Settings(
        app_name="Test",
        debug=True,
        db_path=":memory:",
        plugin_paths=[],
        discover_builtin_channels=False,
        webapi=WebAPIConfigModel(auth_token=""),
    )
    mock = MagicMock(spec=["settings", "is_started", "is_initialized"])
    mock.settings = settings
    mock.is_started = True
    mock.is_initialized = True
    mock.process_supervisor = supervisor
    return mock


def _make_supervisor() -> tuple[ProcessSupervisor, EventBus]:
    bus = EventBus(SimpleNamespace(logger=__import__("logging").getLogger("t")))  # type: ignore[arg-type]
    cfg = ProcessSupervisorConfig(
        enabled=True,
        defaults=ProcessDefaultsConfig(),
        specs={
            "p1": ProcessSpec(
                command=_STAY_ALIVE_ARGV[0], args=_STAY_ALIVE_ARGV[1:], shell=False
            )
        },
    )
    return ProcessSupervisor(cfg, bus), bus


@pytest.fixture
async def processes_client() -> AsyncGenerator[
    tuple[AsyncClient, ProcessSupervisor, EventBus], None
]:
    sup, bus = _make_supervisor()
    await sup.start()
    webapi = WebAPIApp(
        application=_make_app_with_supervisor(sup), host="127.0.0.1", port=6185
    )
    transport = ASGITransport(app=webapi.fastapi_app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, sup, bus
    finally:
        await sup.shutdown()
        await bus.shutdown()


class TestProcessesList:
    async def test_list_returns_registered_processes(self, processes_client) -> None:
        c, sup, _ = processes_client
        resp = await c.get("/api/processes")
        assert resp.status_code == 200
        data = resp.json()
        names = [p["name"] for p in data["processes"]]
        assert "p1" in names

    async def test_detail_returns_one_process(self, processes_client) -> None:
        c, sup, _ = processes_client
        resp = await c.get("/api/processes/p1")
        assert resp.status_code == 200
        info = resp.json()
        assert info["name"] == "p1"
        assert info["status"] == "running"

    async def test_detail_unknown_returns_404(self, processes_client) -> None:
        c, sup, _ = processes_client
        resp = await c.get("/api/processes/nope")
        assert resp.status_code == 404


class TestProcessesLogs:
    async def test_logs_returns_stdout(self, processes_client) -> None:
        c, sup, _ = processes_client
        # Wait for the marker line to land in the buffer.
        for _ in range(40):
            if any("alive" in line for line in sup.get_logs("p1").stdout):
                break
            import asyncio

            await asyncio.sleep(0.05)
        resp = await c.get("/api/processes/p1/logs")
        assert resp.status_code == 200
        data = resp.json()
        assert any("alive" in line for line in data["stdout"])

    async def test_logs_unknown_returns_404(self, processes_client) -> None:
        c, sup, _ = processes_client
        resp = await c.get("/api/processes/nope/logs")
        assert resp.status_code == 404


class TestProcessesControl:
    async def test_restart(self, processes_client) -> None:
        c, sup, _ = processes_client
        first = (await c.get("/api/processes/p1")).json()["pid"]
        resp = await c.post("/api/processes/p1/restart")
        assert resp.status_code == 200
        assert resp.json()["name"] == "p1"
        # Allow the respawn to settle, then pid should differ.
        import asyncio

        for _ in range(60):
            info = (await c.get("/api/processes/p1")).json()
            if (
                info["pid"] is not None
                and info["pid"] != first
                and info["status"] == "running"
            ):
                break
            await asyncio.sleep(0.05)
        assert info["pid"] != first

    async def test_stop_then_start(self, processes_client) -> None:
        c, sup, _ = processes_client
        resp = await c.post("/api/processes/p1/stop")
        assert resp.status_code == 200
        import asyncio

        for _ in range(60):
            info = (await c.get("/api/processes/p1")).json()
            if info["status"] == "stopped":
                break
            await asyncio.sleep(0.05)
        assert info["status"] == "stopped"
        resp = await c.post("/api/processes/p1/start")
        assert resp.status_code == 200
        for _ in range(60):
            info = (await c.get("/api/processes/p1")).json()
            if info["status"] == "running":
                break
            await asyncio.sleep(0.05)
        assert info["status"] == "running"

    async def test_action_unknown_returns_404(self, processes_client) -> None:
        c, sup, _ = processes_client
        assert (await c.post("/api/processes/nope/restart")).status_code == 404
        assert (await c.post("/api/processes/nope/stop")).status_code == 404
        assert (await c.post("/api/processes/nope/start")).status_code == 404


class TestProcessesUnavailable:
    async def test_returns_503_when_supervisor_missing(self) -> None:
        webapi = WebAPIApp(
            application=_make_app_with_supervisor(None), host="127.0.0.1", port=6185
        )
        transport = ASGITransport(app=webapi.fastapi_app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            assert (await c.get("/api/processes")).status_code == 503
