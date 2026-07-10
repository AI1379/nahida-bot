"""Memory management API endpoints."""

from __future__ import annotations

from typing import Any, Literal, cast

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator

from nahida_bot.agent.memory.models import MemoryCandidate, MemoryItem, MemoryRecord
from nahida_bot.agent.memory.scope import SCOPE_ID_GLOBAL, SCOPE_TYPE_GLOBAL
from nahida_bot.agent.memory.service import MemoryService
from nahida_bot.agent.memory.store import StructuredMemoryStore
from nahida_bot.gateway.deps import get_application
from nahida_bot.gateway.services import audit_log
from nahida_bot.workspace.exceptions import WorkspaceError

logger = structlog.get_logger(__name__)

router = APIRouter()

_REQUIRED_STRUCTURED_METHODS = (
    "search_items",
    "append_item",
    "archive_item",
    "list_public_items",
    "search_items_public_all_scopes",
    "search_turns",
    "list_candidates",
)


class MemoryItemResponse(BaseModel):
    item_id: str
    scope_type: str
    scope_id: str
    kind: str
    title: str
    content: str
    status: str = "active"
    confidence: float = 1.0
    importance: float = 0.5
    sensitivity: Literal["public", "private", "secret_like"] = "public"
    sensitivity_source: Literal["default", "dream", "explicit"] = "default"
    source: str = "plugin"
    evidence: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    score: float = 0.0
    parent_id: str = ""
    root_id: str = ""
    node_type: str = "leaf"
    path: str = ""
    source_id: str = ""


class MemoryItemListResponse(BaseModel):
    items: list[MemoryItemResponse]
    total: int
    query: str = ""
    scope_type: str = SCOPE_TYPE_GLOBAL
    scope_id: str = SCOPE_ID_GLOBAL
    limit: int


class MemoryCreateRequest(BaseModel):
    title: str = ""
    content: str
    kind: str = "fact"
    scope_type: str = SCOPE_TYPE_GLOBAL
    scope_id: str = SCOPE_ID_GLOBAL
    sensitivity: Literal["public", "private", "secret_like"] = "public"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    source: str = "webui"
    evidence: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    project_workspace: bool = False
    workspace_id: str | None = None

    @model_validator(mode="after")
    def _validate_content(self) -> "MemoryCreateRequest":
        if not self.content.strip():
            raise ValueError("content cannot be empty")
        if not self.scope_type.strip():
            raise ValueError("scope_type cannot be empty")
        if not self.scope_id.strip():
            raise ValueError("scope_id cannot be empty")
        return self


class MemoryItemActionResponse(BaseModel):
    item_id: str
    status: str


class MemoryCandidateResponse(BaseModel):
    candidate_id: str
    scope_type: str
    scope_id: str
    kind: str
    title: str
    content: str
    status: str = "pending"
    confidence: float = 0.5
    evidence: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


class MemoryCandidateListResponse(BaseModel):
    candidates: list[MemoryCandidateResponse]
    total: int
    status: str = ""
    scope_type: str = SCOPE_TYPE_GLOBAL
    scope_id: str = SCOPE_ID_GLOBAL
    limit: int


class MemoryTurnResponse(BaseModel):
    turn_id: int
    session_id: str
    role: str
    content: str
    source: str = ""
    created_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    keywords: list[str] = Field(default_factory=list)


class MemoryTurnSearchResponse(BaseModel):
    turns: list[MemoryTurnResponse]
    total: int
    query: str = ""
    chat_address: str = ""
    source: str = ""
    role: str = ""
    limit: int


class MemoryProjectRequest(BaseModel):
    scope_type: str = SCOPE_TYPE_GLOBAL
    scope_id: str = SCOPE_ID_GLOBAL
    workspace_id: str | None = None


class MemoryProjectResponse(BaseModel):
    status: str
    workspace_id: str
    scope_type: str
    scope_id: str


def _soft_scope_enabled(app: Any) -> bool:
    try:
        return bool(app.settings.memory.retrieval.soft_scope)
    except AttributeError:
        return False


def _memory_service(app: Any) -> MemoryService:
    store = getattr(app, "memory_store", None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Memory store not initialized",
        )
    if any(
        not callable(getattr(store, name, None))
        for name in _REQUIRED_STRUCTURED_METHODS
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Memory store does not support structured memory APIs",
        )
    return MemoryService(
        cast(StructuredMemoryStore, store),
        soft_scope=_soft_scope_enabled(app),
    )


def _item_response(item: MemoryItem) -> MemoryItemResponse:
    return MemoryItemResponse(
        item_id=item.item_id,
        scope_type=item.scope_type,
        scope_id=item.scope_id,
        kind=item.kind,
        title=item.title,
        content=item.content,
        status=item.status,
        confidence=item.confidence,
        importance=item.importance,
        sensitivity=item.sensitivity,
        sensitivity_source=item.sensitivity_source,
        source=item.source,
        evidence=item.evidence,
        metadata=item.metadata,
        created_at=item.created_at.isoformat() if item.created_at else "",
        updated_at=item.updated_at.isoformat() if item.updated_at else "",
        score=item.score,
        parent_id=item.parent_id,
        root_id=item.root_id,
        node_type=item.node_type,
        path=item.path,
        source_id=item.source_id,
    )


def _candidate_response(candidate: MemoryCandidate) -> MemoryCandidateResponse:
    return MemoryCandidateResponse(
        candidate_id=candidate.candidate_id,
        scope_type=candidate.scope_type,
        scope_id=candidate.scope_id,
        kind=candidate.kind,
        title=candidate.title,
        content=candidate.content,
        status=candidate.status,
        confidence=candidate.confidence,
        evidence=candidate.evidence,
        metadata=candidate.metadata,
        created_at=candidate.created_at.isoformat() if candidate.created_at else "",
        updated_at=candidate.updated_at.isoformat() if candidate.updated_at else "",
    )


def _turn_response(record: MemoryRecord) -> MemoryTurnResponse:
    metadata = record.turn.metadata if isinstance(record.turn.metadata, dict) else {}
    return MemoryTurnResponse(
        turn_id=record.turn_id,
        session_id=record.session_id,
        role=record.turn.role,
        content=record.turn.content,
        source=record.turn.source,
        created_at=record.turn.created_at.isoformat() if record.turn.created_at else "",
        metadata=metadata,
        keywords=record.keywords,
    )


def _workspace_root(app: Any, workspace_id: str | None) -> tuple[str, Any]:
    manager = getattr(app, "workspace_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workspace not initialized",
        )
    try:
        selected = workspace_id or manager.get_active_workspace().workspace_id
        return selected, manager.workspace_path(selected)
    except WorkspaceError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


async def _project_workspace(
    app: Any,
    service: MemoryService,
    *,
    scope_type: str,
    scope_id: str,
    workspace_id: str | None,
) -> MemoryProjectResponse:
    selected_workspace, root = _workspace_root(app, workspace_id)
    await service.project_workspace_memory(
        root,
        scope_type=scope_type,
        scope_id=scope_id,
    )
    audit_log.audit(
        "memory.projected",
        detail=f"{scope_type}:{scope_id} -> {selected_workspace}",
    )
    logger.info(
        "webapi.memory_projected",
        scope_type=scope_type,
        scope_id=scope_id,
        workspace_id=selected_workspace,
    )
    return MemoryProjectResponse(
        status="projected",
        workspace_id=selected_workspace,
        scope_type=scope_type,
        scope_id=scope_id,
    )


@router.get("/api/memory/items", response_model=MemoryItemListResponse)
async def list_memory_items(
    q: str = Query(default=""),
    scope_type: str = Query(default=SCOPE_TYPE_GLOBAL),
    scope_id: str = Query(default=SCOPE_ID_GLOBAL),
    limit: int = Query(default=100, ge=1, le=500),
    app=Depends(get_application),
) -> MemoryItemListResponse:
    service = _memory_service(app)
    scope_type_filter = scope_type.strip() or None
    scope_id_filter = scope_id.strip() or None
    items = await service.list_items(
        query=q,
        scope_type=scope_type_filter,
        scope_id=scope_id_filter,
        limit=limit,
    )
    return MemoryItemListResponse(
        items=[_item_response(item) for item in items],
        total=len(items),
        query=q,
        scope_type=scope_type,
        scope_id=scope_id,
        limit=limit,
    )


@router.post(
    "/api/memory/items",
    response_model=MemoryItemActionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_memory_item(
    body: MemoryCreateRequest,
    app=Depends(get_application),
) -> MemoryItemActionResponse:
    service = _memory_service(app)
    metadata = dict(body.metadata)
    metadata.update(
        {
            "kind": body.kind.strip() or "fact",
            "scope_type": body.scope_type.strip(),
            "scope_id": body.scope_id.strip(),
            "sensitivity": body.sensitivity,
            "source": body.source.strip() or "webui",
            "confidence": body.confidence,
            "importance": body.importance,
            "evidence": body.evidence,
        }
    )
    title = body.title.strip() or body.kind.strip() or "memory"
    item_id = await service.store_item(
        title,
        body.content.strip(),
        metadata=metadata,
    )
    audit_log.audit("memory.item_created", detail=item_id)
    logger.info(
        "webapi.memory_item_created",
        item_id=item_id,
        scope_type=body.scope_type,
        scope_id=body.scope_id,
    )

    if body.project_workspace:
        await _project_workspace(
            app,
            service,
            scope_type=body.scope_type,
            scope_id=body.scope_id,
            workspace_id=body.workspace_id,
        )

    return MemoryItemActionResponse(item_id=item_id, status="created")


@router.delete(
    "/api/memory/items/{item_id}",
    response_model=MemoryItemActionResponse,
)
async def archive_memory_item(
    item_id: str,
    app=Depends(get_application),
) -> MemoryItemActionResponse:
    service = _memory_service(app)
    archived = await service.archive_item(item_id)
    if not archived:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory item not found",
        )
    audit_log.audit("memory.item_archived", detail=item_id)
    logger.info("webapi.memory_item_archived", item_id=item_id)
    return MemoryItemActionResponse(item_id=item_id, status="archived")


@router.get("/api/memory/candidates", response_model=MemoryCandidateListResponse)
async def list_memory_candidates(
    candidate_status: str = Query(default="", alias="status"),
    scope_type: str = Query(default=SCOPE_TYPE_GLOBAL),
    scope_id: str = Query(default=SCOPE_ID_GLOBAL),
    limit: int = Query(default=50, ge=1, le=200),
    app=Depends(get_application),
) -> MemoryCandidateListResponse:
    service = _memory_service(app)
    status_filter = candidate_status.strip() or None
    scope_type_filter = scope_type.strip() or None
    scope_id_filter = scope_id.strip() or None
    candidates = await service.list_candidates(
        status=status_filter,
        scope_type=scope_type_filter,
        scope_id=scope_id_filter,
        limit=limit,
    )
    return MemoryCandidateListResponse(
        candidates=[_candidate_response(candidate) for candidate in candidates],
        total=len(candidates),
        status=candidate_status,
        scope_type=scope_type,
        scope_id=scope_id,
        limit=limit,
    )


@router.get("/api/memory/turns", response_model=MemoryTurnSearchResponse)
async def search_memory_turns(
    q: str = Query(default=""),
    chat_address: str = Query(default=""),
    source: str = Query(default=""),
    role: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=500),
    app=Depends(get_application),
) -> MemoryTurnSearchResponse:
    service = _memory_service(app)
    turns = await service.search_turns(
        q,
        chat_address=chat_address,
        source=source,
        role=role,
        limit=limit,
    )
    return MemoryTurnSearchResponse(
        turns=[_turn_response(record) for record in turns],
        total=len(turns),
        query=q,
        chat_address=chat_address,
        source=source,
        role=role,
        limit=limit,
    )


@router.post("/api/memory/project", response_model=MemoryProjectResponse)
async def project_memory(
    body: MemoryProjectRequest,
    app=Depends(get_application),
) -> MemoryProjectResponse:
    service = _memory_service(app)
    return await _project_workspace(
        app,
        service,
        scope_type=body.scope_type,
        scope_id=body.scope_id,
        workspace_id=body.workspace_id,
    )
