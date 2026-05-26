"""Workspace file management endpoints."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from nahida_bot.gateway.deps import get_application
from nahida_bot.gateway.services import audit_log
from nahida_bot.gateway.services.file_service import (
    create_file,
    list_files,
    read_file,
    rename_entry,
    soft_delete,
    write_file,
)
from nahida_bot.workspace.exceptions import WorkspaceError, WorkspacePathError

logger = structlog.get_logger(__name__)

router = APIRouter()


def _get_sandbox(app, workspace_id: str | None = None):
    if app.workspace_manager is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workspace not initialized",
        )
    try:
        return app.workspace_manager.get_sandbox(workspace_id)
    except WorkspaceError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.get("/api/workspaces")
async def list_workspaces(app=Depends(get_application)):
    if app.workspace_manager is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workspace not initialized",
        )
    workspaces = app.workspace_manager.list_workspaces()
    active = app.workspace_manager.get_active_workspace()
    return {
        "workspaces": [
            {
                "workspace_id": w.workspace_id,
                "is_default": w.is_default,
                "is_active": w.workspace_id == active.workspace_id,
                "created_at": w.created_at.isoformat(),
                "last_active_at": w.last_active_at.isoformat(),
            }
            for w in workspaces
        ],
    }


@router.get("/api/workspaces/active")
async def get_active_workspace(app=Depends(get_application)):
    if app.workspace_manager is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workspace not initialized",
        )
    w = app.workspace_manager.get_active_workspace()
    return {
        "workspace_id": w.workspace_id,
        "is_default": w.is_default,
        "created_at": w.created_at.isoformat(),
        "last_active_at": w.last_active_at.isoformat(),
    }


@router.get("/api/files")
async def list_files_endpoint(
    workspace_id: str | None = Query(None),
    path: str = Query("."),
    app=Depends(get_application),
):
    sandbox = _get_sandbox(app, workspace_id)
    try:
        entries = list_files(sandbox, path)
    except (ValueError, WorkspacePathError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return {
        "path": path,
        "entries": [
            {
                "name": e.name,
                "path": e.path,
                "is_dir": e.is_dir,
                "size": e.size,
                "mtime": e.mtime,
            }
            for e in entries
        ],
    }


@router.get("/api/files/content")
async def get_file_content(
    path: str = Query(...),
    workspace_id: str | None = Query(None),
    app=Depends(get_application),
):
    sandbox = _get_sandbox(app, workspace_id)
    try:
        result = read_file(sandbox, path)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except (ValueError, WorkspacePathError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return {
        "path": result.path,
        "content": result.content,
        "size": result.size,
        "mtime": result.mtime,
    }


class FileWriteBody(BaseModel):
    path: str
    content: str
    workspace_id: str | None = None


@router.put("/api/files/content")
async def write_file_content(
    body: FileWriteBody,
    app=Depends(get_application),
):
    sandbox = _get_sandbox(app, body.workspace_id)
    try:
        result = write_file(sandbox, body.path, body.content)
    except (ValueError, WorkspacePathError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    audit_log.audit("file.written", detail=body.path)
    logger.info("webapi.file_written", path=body.path, size=result.size)
    return {
        "path": result.path,
        "size": result.size,
        "mtime": result.mtime,
    }


class FileCreateBody(BaseModel):
    path: str
    content: str = ""
    workspace_id: str | None = None


@router.post("/api/files/create")
async def create_file_endpoint(
    body: FileCreateBody,
    app=Depends(get_application),
):
    sandbox = _get_sandbox(app, body.workspace_id)
    try:
        result = create_file(sandbox, body.path, body.content)
    except FileExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except (ValueError, WorkspacePathError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    audit_log.audit("file.created", detail=body.path)
    logger.info("webapi.file_created", path=body.path)
    return {
        "path": result.path,
        "size": result.size,
        "mtime": result.mtime,
    }


class FileRenameBody(BaseModel):
    path: str
    new_name: str
    workspace_id: str | None = None


@router.post("/api/files/rename")
async def rename_file_endpoint(
    body: FileRenameBody,
    app=Depends(get_application),
):
    sandbox = _get_sandbox(app, body.workspace_id)
    try:
        new_path = rename_entry(sandbox, body.path, body.new_name)
    except (FileNotFoundError, FileExistsError, ValueError, WorkspacePathError) as exc:
        status_code = (
            status.HTTP_404_NOT_FOUND
            if isinstance(exc, FileNotFoundError)
            else status.HTTP_409_CONFLICT
            if isinstance(exc, FileExistsError)
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    audit_log.audit("file.renamed", detail=f"{body.path} -> {new_path}")
    logger.info("webapi.file_renamed", old_path=body.path, new_path=new_path)
    return {"path": new_path}


class FileDeleteBody(BaseModel):
    path: str
    workspace_id: str | None = None


@router.post("/api/files/delete")
async def delete_file_endpoint(
    body: FileDeleteBody,
    app=Depends(get_application),
):
    sandbox = _get_sandbox(app, body.workspace_id)
    try:
        trash_path = soft_delete(sandbox, body.path)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except (ValueError, WorkspacePathError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    audit_log.audit("file.deleted", detail=f"{body.path} -> {trash_path}")
    logger.info("webapi.file_deleted", path=body.path, trash_path=trash_path)
    return {"status": "deleted", "trash_path": trash_path}
