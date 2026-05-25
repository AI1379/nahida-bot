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
from nahida_bot.scheduler.models import CronJob

router = APIRouter()


def _require_scheduler(app):
    if app.scheduler_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scheduler not initialized",
        )
    return app.scheduler_service


@router.get("/api/cron/jobs", response_model=CronListResponse)
async def list_all_cron_jobs(
    active: str = Query("all"),
    limit: int = Query(100, ge=1, le=500),
    app=Depends(get_application),
) -> CronListResponse:
    """Global cron list for admin WebUI — all jobs across all chats.

    Query param ``active``: ``true`` = active only, ``false`` = inactive only,
    ``all`` (default) = everything.
    """
    svc = _require_scheduler(app)
    jobs = await svc.list_all_jobs(active=active, limit=limit)
    return CronListResponse(jobs=[_job_to_response(j) for j in jobs])


@router.get("/api/cron", response_model=CronListResponse)
async def list_cron_jobs(
    target: str | None = Query(None),
    platform: str = Query(""),
    chat_id: str = Query(""),
    app=Depends(get_application),
) -> CronListResponse:
    svc = _require_scheduler(app)
    address = _resolve_read_target(
        target=target,
        platform=platform,
        chat_id=chat_id,
    )
    jobs = await svc.list_jobs(address)
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

    address = _resolve_write_target(body.target)

    try:
        job = await svc.create_job(
            address=address,
            prompt=body.prompt,
            mode=body.mode,
            fire_at=body.fire_at,
            interval_seconds=body.interval_seconds,
            cron_expression=body.cron_expression,
            max_runs=body.max_runs,
            session_mode=body.session_mode,
            session_name=body.session_name,
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
            mode=body.mode,
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


def _job_to_response(job: CronJob) -> CronJobResponse:
    return CronJobResponse(
        job_id=job.job_id,
        platform=job.platform,
        chat_id=job.chat_id,
        mode=job.mode,
        prompt=job.prompt,
        is_active=job.is_active,
        next_fire_at=job.next_fire_at,
        run_count=job.run_count,
        created_at=job.created_at,
        session_mode=job.session_mode,
        session_name=job.session_name,
        # Extended fields
        session_key=job.session_key,
        chat_type=job.chat_type,
        last_fired_at=job.last_fired_at,
        failure_count=job.failure_count,
        last_error=job.last_error,
        claimed_at=job.claimed_at,
        workspace_id=job.workspace_id,
        fire_at=job.fire_at,
        interval_seconds=job.interval_seconds,
        cron_expression=job.cron_expression,
        max_runs=job.max_runs,
    )


def _resolve_read_target(
    *,
    target: str | None,
    platform: str,
    chat_id: str,
) -> ChatAddress:
    if target:
        try:
            address = ChatAddress.parse(target)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        return address
    if platform and chat_id:
        return ChatAddress(channel=platform, target_type="unknown", target_id=chat_id)
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Provide 'target' or both 'platform' and 'chat_id'.",
    )


def _resolve_write_target(target: str) -> ChatAddress:
    if not target:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide a typed 'target' such as 'milky:group:20001'.",
        )
    try:
        address = ChatAddress.parse(target)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    if not address.is_typed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="target must include a chat type, such as private or group.",
        )
    return address
