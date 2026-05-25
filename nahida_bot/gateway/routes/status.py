"""System status endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from nahida_bot.gateway.deps import get_application
from nahida_bot.gateway.schemas import StatusResponse
from nahida_bot.gateway.services.status_service import collect_status

router = APIRouter()


@router.get("/api/status", response_model=StatusResponse)
async def get_status(app=Depends(get_application)) -> StatusResponse:
    status = collect_status(app)
    usage = app._usage_ledger.get_totals() if app._usage_ledger else None

    return StatusResponse(
        app={
            "name": status.name,
            "version": status.version,
            "debug": status.debug,
            "started": status.started,
            "started_at": status.started_at,
            "uptime_seconds": status.uptime_seconds,
            "pid": status.pid,
        },
        resources={
            "cpu_percent": status.resources.cpu_percent,
            "memory_rss_bytes": status.resources.memory_rss_bytes,
            "memory_percent": status.resources.memory_percent,
            "disk_free_bytes": status.resources.disk_free_bytes,
            "db_size_bytes": status.resources.db_size_bytes,
            "workspace_size_bytes": status.resources.workspace_size_bytes,
        },
        services={
            "router": status.services.router,
            "scheduler": status.services.scheduler,
            "webapi": status.services.webapi,
            "memory": status.services.memory,
            "workspace": status.services.workspace,
        },
        usage={
            "input_tokens": usage.input_tokens if usage else 0,
            "output_tokens": usage.output_tokens if usage else 0,
            "cached_tokens": usage.cached_tokens if usage else 0,
            "reasoning_tokens": usage.reasoning_tokens if usage else 0,
            "estimated_cost": usage.estimated_cost if usage else None,
            "currency": None,
        },
    )
