"""Plugin management endpoints."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol, runtime_checkable

import structlog
from fastapi import APIRouter, Depends, HTTPException, status

from nahida_bot.core.exceptions import PluginLoadError, PluginStateError
from nahida_bot.gateway.deps import get_application
from nahida_bot.gateway.schemas import (
    PluginActionResponse,
    PluginListResponse,
    PluginSummaryResponse,
)
from nahida_bot.plugins.manager import PluginRecord

logger = structlog.get_logger(__name__)

router = APIRouter()


@runtime_checkable
class _ModelDumpable(Protocol):
    def model_dump(self) -> Mapping[str, Any]: ...


def _require_plugin_manager(app):
    if app.plugin_manager is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Plugin manager not initialized",
        )
    return app.plugin_manager


@router.get("/api/plugins", response_model=PluginListResponse)
async def list_plugins(app=Depends(get_application)) -> PluginListResponse:
    manager = _require_plugin_manager(app)
    records = sorted(
        manager.list_plugins(),
        key=lambda record: (record.manifest.load_phase, record.manifest.id),
    )
    return PluginListResponse(
        plugins=[_record_to_response(record) for record in records]
    )


@router.get("/api/plugins/{plugin_id}", response_model=PluginSummaryResponse)
async def get_plugin(
    plugin_id: str,
    app=Depends(get_application),
) -> PluginSummaryResponse:
    manager = _require_plugin_manager(app)
    record = manager.get_record(plugin_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plugin not found",
        )
    return _record_to_response(record)


@router.post("/api/plugins/{plugin_id}/load", response_model=PluginActionResponse)
async def load_plugin(
    plugin_id: str,
    app=Depends(get_application),
) -> PluginActionResponse:
    manager = _require_plugin_manager(app)
    return await _run_plugin_action(
        plugin_id,
        "load",
        manager.get_record,
        manager.load,
    )


@router.post("/api/plugins/{plugin_id}/enable", response_model=PluginActionResponse)
async def enable_plugin(
    plugin_id: str,
    app=Depends(get_application),
) -> PluginActionResponse:
    manager = _require_plugin_manager(app)
    return await _run_plugin_action(
        plugin_id,
        "enable",
        manager.get_record,
        manager.enable,
    )


@router.post("/api/plugins/{plugin_id}/disable", response_model=PluginActionResponse)
async def disable_plugin(
    plugin_id: str,
    app=Depends(get_application),
) -> PluginActionResponse:
    manager = _require_plugin_manager(app)
    return await _run_plugin_action(
        plugin_id,
        "disable",
        manager.get_record,
        manager.disable,
    )


@router.post("/api/plugins/{plugin_id}/reload", response_model=PluginActionResponse)
async def reload_plugin(
    plugin_id: str,
    app=Depends(get_application),
) -> PluginActionResponse:
    manager = _require_plugin_manager(app)
    return await _run_plugin_action(
        plugin_id,
        "reload",
        manager.get_record,
        manager.reload,
    )


@router.post("/api/plugins/{plugin_id}/unload", response_model=PluginActionResponse)
async def unload_plugin(
    plugin_id: str,
    app=Depends(get_application),
) -> PluginActionResponse:
    manager = _require_plugin_manager(app)
    return await _run_plugin_action(
        plugin_id,
        "unload",
        manager.get_record,
        manager.unload,
    )


async def _run_plugin_action(
    plugin_id: str,
    action: str,
    get_record: Callable[[str], PluginRecord | None],
    operation: Callable[[str], Awaitable[None]],
) -> PluginActionResponse:
    if get_record(plugin_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plugin not found",
        )

    try:
        await operation(plugin_id)
    except PluginStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except PluginLoadError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    record = get_record(plugin_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plugin not found",
        )

    logger.info("webapi.plugin_action", plugin_id=plugin_id, action=action)
    return PluginActionResponse(
        plugin_id=plugin_id,
        action=action,
        state=record.state.value,
        status="ok",
    )


def _record_to_response(record: PluginRecord) -> PluginSummaryResponse:
    manifest = record.manifest
    config = manifest.config or {}
    return PluginSummaryResponse(
        id=manifest.id,
        name=manifest.name,
        version=manifest.version,
        description=manifest.description,
        state=record.state.value,
        configured_enabled=record.configured_enabled,
        path=str(record.plugin_dir),
        entrypoint=manifest.entrypoint,
        load_phase=manifest.load_phase,
        nahida_bot_version=manifest.nahida_bot_version,
        sdk_version=manifest.sdk_version,
        error_message=record.error_message,
        permissions=_dump_model(manifest.permissions),
        capabilities=_dump_model(manifest.capabilities),
        depends_on=[_dump_model(item) for item in manifest.depends_on],
        config_keys=sorted(str(key) for key in config.keys()),
        config_schema=dict(manifest.config_schema or {}),
        has_config=bool(config),
        has_instance=record.instance is not None,
        has_runtime_api=record.api_bridge is not None,
    )


def _dump_model(value: object) -> dict[str, Any]:
    if isinstance(value, _ModelDumpable):
        return _dump_mapping(value.model_dump())
    if isinstance(value, Mapping):
        return _dump_mapping(value)
    return {}


def _dump_mapping(value: Mapping[Any, Any]) -> dict[str, Any]:
    return {str(key): item for key, item in value.items()}
