"""WebUI bootstrap and system action endpoints.

Bootstrap is unauthenticated (it tells the UI how to authenticate).
System actions (restart/shutdown) are dangerous and require auth.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status

from nahida_bot.gateway.deps import get_application
from nahida_bot.gateway.schemas import (
    BootstrapResponse,
    SystemActionRequest,
    SystemActionResponse,
)
from nahida_bot.gateway.services import audit_log

# Public: no auth required
bootstrap_router = APIRouter()

# Admin: auth required
system_router = APIRouter()


@bootstrap_router.get("/api/webui/bootstrap", response_model=BootstrapResponse)
async def get_bootstrap(app=Depends(get_application)) -> BootstrapResponse:
    return BootstrapResponse(
        app_name=app.settings.app_name,
        version=app.version,
        api_base="/api",
        webui_base="/ui",
        auth={
            "required": bool(app.settings.webapi.auth_token),
            "mode": "bearer",
        },
        features=[
            {"id": "home", "route": "/", "label": "Overview", "scope": "operator.read"},
            {
                "id": "config",
                "route": "/config",
                "label": "Config",
                "scope": "operator.admin",
            },
            {"id": "cron", "route": "/cron", "label": "CRON", "scope": "operator.read"},
            {
                "id": "sessions",
                "route": "/sessions",
                "label": "Sessions",
                "scope": "operator.read",
            },
            {
                "id": "files",
                "route": "/files",
                "label": "Files",
                "scope": "operator.read",
            },
        ],
        server_time=datetime.now(UTC).isoformat(),
    )


@system_router.post("/api/system/actions/restart", response_model=SystemActionResponse)
async def system_restart(
    body: SystemActionRequest,
    app=Depends(get_application),
) -> SystemActionResponse:
    if not body.confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="confirm=true is required",
        )

    audit_log.audit("system.restart_requested", detail=body.reason)

    # Request shutdown; external supervisor must restart.
    app.request_shutdown()

    return SystemActionResponse(
        accepted=True,
        action="restart",
        mode="supervisor_required",
        message="Shutdown requested; external supervisor must restart the process.",
    )


@system_router.post("/api/system/actions/shutdown", response_model=SystemActionResponse)
async def system_shutdown(
    body: SystemActionRequest,
    app=Depends(get_application),
) -> SystemActionResponse:
    if not body.confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="confirm=true is required",
        )

    audit_log.audit("system.shutdown_requested", detail=body.reason)

    app.request_shutdown()

    return SystemActionResponse(
        accepted=True,
        action="shutdown",
        mode="shutdown_only",
        message="Shutdown requested. Process will exit.",
    )
