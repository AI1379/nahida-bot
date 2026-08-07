"""Supervised process management endpoints.

Exposes the core :class:`ProcessSupervisor` (SSH tunnels, frpc, cloudflared,
sidecars) to the WebUI. Only already-declared processes (from ``config.yaml``)
can be started/stopped/restarted — no runtime arbitrary-command execution.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status

from nahida_bot.core.process_supervisor import ProcessInfo, ProcessLogs
from nahida_bot.gateway.deps import get_application
from nahida_bot.gateway.schemas import (
    ProcessActionResponse,
    ProcessInfoResponse,
    ProcessListResponse,
    ProcessLogsResponse,
)

logger = structlog.get_logger(__name__)

router = APIRouter()


def _require_supervisor(app):
    sup = getattr(app, "process_supervisor", None)
    if sup is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Process supervisor not initialized",
        )
    return sup


def _to_response(info: ProcessInfo) -> ProcessInfoResponse:
    return ProcessInfoResponse(
        name=info.name,
        owner=info.owner,
        status=info.status,
        pid=info.pid,
        restart_count=info.restart_count,
        exit_code=info.exit_code,
        started_at=info.started_at.isoformat() if info.started_at else None,
        last_error=info.last_error,
        health=info.health,
        restart_policy=info.restart_policy,
        command=info.command,
    )


@router.get("/api/processes", response_model=ProcessListResponse)
async def list_processes(app=Depends(get_application)) -> ProcessListResponse:
    sup = _require_supervisor(app)
    processes = [_to_response(i) for i in sup.list_processes()]
    processes.sort(key=lambda p: p.name)
    return ProcessListResponse(processes=processes)


@router.get("/api/processes/{name}", response_model=ProcessInfoResponse)
async def get_process(
    name: str,
    app=Depends(get_application),
) -> ProcessInfoResponse:
    sup = _require_supervisor(app)
    info = sup.get_process(name)
    if info is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Process not found",
        )
    return _to_response(info)


@router.get("/api/processes/{name}/logs", response_model=ProcessLogsResponse)
async def get_process_logs(
    name: str,
    stream: str = Query(default="both", pattern="^(both|stdout|stderr)$"),
    limit: int = Query(default=200, ge=1, le=10000),
    app=Depends(get_application),
) -> ProcessLogsResponse:
    sup = _require_supervisor(app)
    if sup.get_process(name) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Process not found",
        )
    logs: ProcessLogs = sup.get_logs(name, stream=stream, limit=limit)
    return ProcessLogsResponse(
        name=logs.name,
        stdout=logs.stdout,
        stderr=logs.stderr,
    )


@router.post("/api/processes/{name}/start", response_model=ProcessActionResponse)
async def start_process(
    name: str,
    app=Depends(get_application),
) -> ProcessActionResponse:
    sup = _require_supervisor(app)
    if sup.get_process(name) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Process not found",
        )
    try:
        info = await sup.start_one(name)
    except Exception as exc:  # noqa: BLE001
        logger.exception("processes.start_failed", name=name)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    return ProcessActionResponse(name=info.name, status=info.status)


@router.post("/api/processes/{name}/stop", response_model=ProcessActionResponse)
async def stop_process(
    name: str,
    app=Depends(get_application),
) -> ProcessActionResponse:
    sup = _require_supervisor(app)
    if sup.get_process(name) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Process not found",
        )
    try:
        info = await sup.stop(name)
    except Exception as exc:  # noqa: BLE001
        logger.exception("processes.stop_failed", name=name)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    return ProcessActionResponse(name=info.name, status=info.status)


@router.post("/api/processes/{name}/restart", response_model=ProcessActionResponse)
async def restart_process(
    name: str,
    app=Depends(get_application),
) -> ProcessActionResponse:
    sup = _require_supervisor(app)
    if sup.get_process(name) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Process not found",
        )
    try:
        info = await sup.restart(name)
    except Exception as exc:  # noqa: BLE001
        logger.exception("processes.restart_failed", name=name)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    return ProcessActionResponse(name=info.name, status=info.status)
