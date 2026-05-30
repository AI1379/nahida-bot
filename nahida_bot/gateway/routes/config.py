"""Config management endpoints."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from nahida_bot.gateway.deps import get_application
from nahida_bot.gateway.schemas import (
    ConfigCurrentResponse,
    ConfigSaveRequest,
    ConfigSaveResponse,
    ConfigSchemaResponse,
    ConfigValidateResponse,
    ConfigValueResponse,
)
from nahida_bot.gateway.services import audit_log
from nahida_bot.gateway.services.config_service import (
    flatten_yaml_values,
    list_backups,
    read_current_config,
    redact_yaml,
    save_config_with_backup,
    validate_config_text,
)
from nahida_bot.core.config_schema import build_config_schema

logger = structlog.get_logger(__name__)

router = APIRouter()


def _config_path(app) -> str | None:
    """Return the config YAML path from the running Application, if known."""
    return getattr(app, "_config_yaml_path", None)


@router.get("/api/config/current", response_model=ConfigCurrentResponse)
async def get_current_config(
    redact: bool = Query(True),
    app=Depends(get_application),
) -> ConfigCurrentResponse:
    try:
        cfg = read_current_config(config_path=_config_path(app))
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    raw = redact_yaml(cfg.raw) if redact else cfg.raw
    return ConfigCurrentResponse(
        content=raw,
        checksum=cfg.checksum,
        path=cfg.path,
        mtime=cfg.mtime,
        entries=[
            ConfigValueResponse(path=e.path, type=e.type_, value=e.value)
            for e in flatten_yaml_values(raw)
        ],
    )


@router.get("/api/config/schema", response_model=ConfigSchemaResponse)
async def get_config_schema(
    section: str | None = Query(None),
    include_plugins: bool = Query(True),
    app=Depends(get_application),
) -> ConfigSchemaResponse:
    entries = build_config_schema(
        section=section,
        show_providers=True,
        show_plugins=include_plugins,
        config_yaml=_config_path(app),
    )
    return ConfigSchemaResponse(
        entries=[
            {
                "path": e.path,
                "type": e.type_,
                "default": e.default_,
                "constraints": e.constraints,
            }
            for e in entries
        ]
    )


@router.post("/api/config/validate", response_model=ConfigValidateResponse)
async def validate_config(
    body: dict,
    app=Depends(get_application),
) -> ConfigValidateResponse:
    content = body.get("content", "")
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing 'content' field",
        )
    report = validate_config_text(content)
    logger.info(
        "webapi.config_validated",
        errors=report.errors,
        warnings=report.warnings,
    )
    return ConfigValidateResponse(
        errors=report.errors,
        warnings=report.warnings,
        ok=report.ok,
        issues=[
            {
                "severity": i.severity,
                "path": i.path,
                "message": i.message,
            }
            for i in report.issues
        ],
    )


@router.put("/api/config/current", response_model=ConfigSaveResponse)
async def save_current_config(
    request: Request,
    body: ConfigSaveRequest,
    app=Depends(get_application),
) -> ConfigSaveResponse:
    result = save_config_with_backup(
        content=body.content,
        expected_checksum=body.expected_checksum,
        config_path=_config_path(app),
    )
    if not result.saved and result.validation and result.validation.errors > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Validation failed or checksum mismatch",
                "issues": [
                    {
                        "severity": i.severity,
                        "path": i.path,
                        "message": i.message,
                    }
                    for i in result.validation.issues
                ],
            },
        )

    audit_log.audit("config.saved", detail=f"backup={result.backup_path}")

    # Notify SSE clients
    broadcaster = getattr(request.app.state, "event_broadcaster", None)
    if broadcaster is not None and result.saved:
        broadcaster.notify_config_saved(result.backup_path, result.restart_required)

    logger.info(
        "webapi.config_saved",
        saved=result.saved,
        backup=result.backup_path,
        restart_required=result.restart_required,
    )
    return ConfigSaveResponse(
        saved=result.saved,
        backup_path=result.backup_path,
        checksum=result.checksum,
        restart_required=result.restart_required,
        validation={
            "errors": result.validation.errors if result.validation else 0,
            "warnings": result.validation.warnings if result.validation else 0,
            "issues": [
                {
                    "severity": i.severity,
                    "path": i.path,
                    "message": i.message,
                }
                for i in (result.validation.issues if result.validation else [])
            ],
        },
    )


@router.get("/api/config/backups")
async def get_config_backups(
    app=Depends(get_application),
):
    backups = list_backups(config_path=_config_path(app))
    return {"backups": backups}
