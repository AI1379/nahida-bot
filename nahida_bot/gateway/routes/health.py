"""Health check endpoint."""

from fastapi import APIRouter, Depends

from nahida_bot.gateway.deps import get_application
from nahida_bot.gateway.schemas import HealthResponse

router = APIRouter()


@router.get("/api/health", response_model=HealthResponse)
async def health_check(app=Depends(get_application)) -> HealthResponse:
    return HealthResponse(
        status="ok" if app.is_started else "degraded",
        app_name=app.settings.app_name,
        started=app.is_started,
    )
