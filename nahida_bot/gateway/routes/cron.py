"""Cron job endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query, status

from nahida_bot.gateway.deps import get_application
from nahida_bot.gateway.schemas import (
    CreateCronRequest,
    CreateCronResponse,
    CronJobResponse,
    CronListResponse,
)

router = APIRouter()


@router.get("/api/cron", response_model=CronListResponse)
async def list_cron_jobs(
    platform: str = Query(...),
    chat_id: str = Query(...),
    app=Depends(get_application),
) -> CronListResponse:
    if app.scheduler_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scheduler not initialized",
        )
    jobs = await app.scheduler_service.list_jobs(platform, chat_id)
    return CronListResponse(jobs=[_job_to_response(j) for j in jobs])


@router.post(
    "/api/cron", response_model=CreateCronResponse, status_code=status.HTTP_201_CREATED
)
async def create_cron_job(
    body: CreateCronRequest,
    app=Depends(get_application),
) -> CreateCronResponse:
    if app.scheduler_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scheduler not initialized",
        )
    try:
        job = await app.scheduler_service.create_job(
            platform=body.platform,
            chat_id=body.chat_id,
            prompt=body.prompt,
            mode=body.mode,  # type: ignore[arg-type]
            fire_at=body.fire_at,
            interval_seconds=body.interval_seconds,
            cron_expression=body.cron_expression,
            max_runs=body.max_runs,
            session_mode=body.session_mode,  # type: ignore[arg-type]
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    return CreateCronResponse(job_id=job.job_id, status="created")


def _job_to_response(job: object) -> CronJobResponse:
    return CronJobResponse(
        job_id=job.job_id,  # type: ignore[union-attr]
        platform=job.platform,  # type: ignore[union-attr]
        chat_id=job.chat_id,  # type: ignore[union-attr]
        mode=job.mode,  # type: ignore[union-attr]
        prompt=job.prompt,  # type: ignore[union-attr]
        is_active=job.is_active,  # type: ignore[union-attr]
        next_fire_at=job.next_fire_at,  # type: ignore[union-attr]
        run_count=job.run_count,  # type: ignore[union-attr]
        created_at=job.created_at,  # type: ignore[union-attr]
    )
