"""Workspace skill listing and content endpoints."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, status

from nahida_bot.gateway.deps import get_application

logger = structlog.get_logger(__name__)

router = APIRouter()


def _get_workspace(app):
    """Resolve the manager and active workspace identity."""
    if app.workspace_manager is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workspace not initialized",
        )
    active = app.workspace_manager.get_active_workspace()
    return app.workspace_manager, active.workspace_id


@router.get("/api/skills")
async def list_skills(app=Depends(get_application)):
    """List all installed workspace skills (name + description only)."""
    manager, workspace_id = _get_workspace(app)
    workspace_root = manager.workspace_path(workspace_id)
    catalog = manager.list_skills(workspace_id)
    return {
        "skills": [
            {
                "name": s.name,
                "description": s.description,
                "file_path": str(s.file_path.relative_to(workspace_root))
                if workspace_root in s.file_path.parents
                else s.file_path.as_posix(),
            }
            for s in catalog
        ],
        "total": len(catalog),
    }


@router.get("/api/skills/{name}")
async def get_skill(name: str, app=Depends(get_application)):
    """Get the full formatted content of a workspace skill by name."""
    manager, workspace_id = _get_workspace(app)
    content = manager.read_skill(workspace_id, name)
    if content is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{name}' not found",
        )
    return {"name": name, "content": content}
