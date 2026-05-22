"""Cron job endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query, status

from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.gateway.deps import get_application
from nahida_bot.gateway.schemas import (
    CreateCronRequest,
    CreateCronResponse,
    CronActionResponse,
    CronJobResponse,
    CronListResponse,
    UpdateCronRequest,
)

router = APIRouter()


def _require_scheduler(app):
    if app.scheduler_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scheduler not initialized",
        )
    return app.scheduler_service


@router.get("/api/cron", response_model=CronListResponse)
async def list_cron_jobs(
    target: str | None = Query(None),
    platform: str = Query(""),
    chat_id: str = Query(""),
    app=Depends(get_application),
) -> CronListResponse:
    svc = _require_scheduler(app)
    platform, chat_id, chat_type = _resolve_target_fields(
        target=target,
        platform=platform,
        chat_id=chat_id,
    )
    jobs = await svc.list_jobs(platform, chat_id, chat_type=chat_type)
    return CronListResponse(jobs=[_job_to_response(j) for j in jobs])


@router.get("/api/cron/{job_id}", response_model=CronJobResponse)
async def get_cron_job(
    job_id: str,
    app=Depends(get_application),
) -> CronJobResponse:
    svc = _require_scheduler(app)
    job = await svc.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        )
    return _job_to_response(job)


@router.post(
    "/api/cron", response_model=CreateCronResponse, status_code=status.HTTP_201_CREATED
)
async def create_cron_job(
    body: CreateCronRequest,
    app=Depends(get_application),
) -> CreateCronResponse:
    svc = _require_scheduler(app)

    # Resolve platform/chat_id/chat_type from target or legacy fields
    platform, chat_id, chat_type = _resolve_target_fields(
        target=body.target,
        platform=body.platform,
        chat_id=body.chat_id,
    )

    try:
        job = await svc.create_job(
            platform=platform,
            chat_id=chat_id,
            prompt=body.prompt,
            mode=body.mode,  # type: ignore[arg-type]
            fire_at=body.fire_at,
            interval_seconds=body.interval_seconds,
            cron_expression=body.cron_expression,
            max_runs=body.max_runs,
            session_mode=body.session_mode,  # type: ignore[arg-type]
            chat_type=chat_type,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    return CreateCronResponse(job_id=job.job_id, status="created")


@router.patch("/api/cron/{job_id}", response_model=CronJobResponse)
async def update_cron_job(
    job_id: str,
    body: UpdateCronRequest,
    app=Depends(get_application),
) -> CronJobResponse:
    svc = _require_scheduler(app)
    try:
        job = await svc.update_job(
            job_id,
            prompt=body.prompt,
            mode=body.mode,  # type: ignore[arg-type]
            fire_at=body.fire_at,
            interval_seconds=body.interval_seconds,
            cron_expression=body.cron_expression,
            max_runs=body.max_runs,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    return _job_to_response(job)


@router.post("/api/cron/{job_id}/cancel", response_model=CronActionResponse)
async def cancel_cron_job(
    job_id: str,
    app=Depends(get_application),
) -> CronActionResponse:
    svc = _require_scheduler(app)
    cancelled = await svc.cancel_job(job_id)
    if not cancelled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found or already inactive",
        )
    return CronActionResponse(job_id=job_id, status="cancelled")


@router.delete("/api/cron/{job_id}", response_model=CronActionResponse)
async def delete_cron_job(
    job_id: str,
    app=Depends(get_application),
) -> CronActionResponse:
    svc = _require_scheduler(app)
    deleted = await svc.delete_job(job_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )
    return CronActionResponse(job_id=job_id, status="deleted")


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


def _resolve_target_fields(
    *,
    target: str | None,
    platform: str,
    chat_id: str,
) -> tuple[str, str, str]:
    if target:
        try:
            address = ChatAddress.parse(target)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        return (
            address.channel,
            address.target_id,
            address.target_type if address.is_typed else "",
        )
    if platform and chat_id:
        return platform, chat_id, ""
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Provide 'target' or both 'platform' and 'chat_id'.",
    )
