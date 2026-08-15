"""Config management endpoints."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from nahida_bot.gateway.deps import get_application
from nahida_bot.gateway.schemas import (
    ConfigCurrentResponse,
    ConfigDocumentResponse,
    ConfigPatchRequest,
    ConfigRestoreRequest,
    ConfigSaveRequest,
    ConfigSaveResponse,
    ConfigSchemaResponse,
    ConfigValidateResponse,
    ConfigValueResponse,
)
from nahida_bot.gateway.services import audit_log
from nahida_bot.gateway.services.config_service import (
    config_data_to_yaml,
    flatten_yaml_values,
    list_backups,
    read_config_document,
    read_current_config,
    redact_yaml,
    restore_config_backup as restore_config_backup_service,
    save_config_patch_with_backup,
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


@router.get("/api/config/document", response_model=ConfigDocumentResponse)
async def get_config_document(
    app=Depends(get_application),
) -> ConfigDocumentResponse:
    try:
        doc = read_config_document(config_path=_config_path(app))
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    return ConfigDocumentResponse(
        content=doc.redacted_raw,
        checksum=doc.checksum,
        path=doc.path,
        mtime=doc.mtime,
        data=doc.data,
        redacted_data=doc.redacted_data,
        redacted_paths=doc.redacted_paths,
        entries=[
            ConfigValueResponse(path=e.path, type=e.type_, value=e.value)
            for e in doc.entries
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
    if not content and "data" in body:
        data = body.get("data")
        if not isinstance(data, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="'data' must be a JSON object",
            )
        content = config_data_to_yaml(data)

    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing 'content' or 'data' field",
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


@router.patch("/api/config/current", response_model=ConfigSaveResponse)
async def patch_current_config(
    request: Request,
    body: ConfigPatchRequest,
    app=Depends(get_application),
) -> ConfigSaveResponse:
    result = save_config_patch_with_backup(
        changes=[change.model_dump(exclude_unset=True) for change in body.changes],
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

    audit_log.audit("config.patch_saved", detail=f"backup={result.backup_path}")

    broadcaster = getattr(request.app.state, "event_broadcaster", None)
    if broadcaster is not None and result.saved:
        broadcaster.notify_config_saved(result.backup_path, result.restart_required)

    logger.info(
        "webapi.config_patch_saved",
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


@router.post("/api/config/backups/{backup_name}/restore")
async def restore_config_backup(
    backup_name: str,
    body: ConfigRestoreRequest | None = None,
    app=Depends(get_application),
):
    expected = body.expected_checksum if body is not None else None
    result = restore_config_backup_service(
        backup_name,
        config_path=_config_path(app),
        expected_checksum=expected,
    )
    if not result.saved:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Restore rejected (missing backup, validation "
                "failed, or checksum mismatch)",
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

    audit_log.audit("config.backup_restored", detail=f"backup={backup_name}")
    logger.info(
        "webapi.config_backup_restored",
        backup=backup_name,
        new_backup=result.backup_path,
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
            "issues": [],
        },
    )
