"""Workspace skill listing and content endpoints."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, status

from nahida_bot.agent.context import SkillCatalog
from nahida_bot.gateway.deps import get_application

logger = structlog.get_logger(__name__)

router = APIRouter()


def _get_workspace_root(app):
    """Resolve the active workspace root for the application."""
    if app.workspace_manager is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workspace not initialized",
        )
    active = app.workspace_manager.get_active_workspace()
    return app.workspace_manager.workspace_path(active.workspace_id)


@router.get("/api/skills")
async def list_skills(app=Depends(get_application)):
    """List all installed workspace skills (name + description only)."""
    workspace_root = _get_workspace_root(app)
    catalog = SkillCatalog.scan_catalog(workspace_root)
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
    workspace_root = _get_workspace_root(app)
    content = SkillCatalog.load_skill_content(workspace_root, name)
    if content is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{name}' not found",
        )
    return {"name": name, "content": content}
