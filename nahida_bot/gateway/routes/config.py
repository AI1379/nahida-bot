"""Config management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from nahida_bot.gateway.deps import get_application
from nahida_bot.gateway.schemas import (
    ConfigCurrentResponse,
    ConfigSaveRequest,
    ConfigSaveResponse,
    ConfigSchemaResponse,
    ConfigValidateResponse,
)
from nahida_bot.gateway.services import audit_log
from nahida_bot.gateway.services.config_service import (
    list_backups,
    read_current_config,
    redact_yaml,
    save_config_with_backup,
    validate_config_text,
)
from nahida_bot.core.config_schema import build_config_schema

router = APIRouter()


def _config_path(app) -> str | None:
    """Return the config YAML path from the running Application, if known."""
    return app._config_yaml_path


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
