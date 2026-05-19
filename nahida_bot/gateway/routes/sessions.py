"""Session listing and history endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query, status

from nahida_bot.gateway.deps import get_application
from nahida_bot.gateway.schemas import (
    SessionHistoryResponse,
    SessionListResponse,
    SessionSummaryResponse,
    TurnResponse,
)

router = APIRouter()


@router.get("/api/sessions", response_model=SessionListResponse)
async def list_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    app=Depends(get_application),
) -> SessionListResponse:
    if app.memory_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Memory store not initialized",
        )
    summaries = await app.memory_store.list_sessions(limit=limit)
    return SessionListResponse(
        sessions=[
            SessionSummaryResponse(
                session_id=s.session_id,
                workspace_id=s.workspace_id,
                created_at=s.created_at,
                last_active_at=s.last_active_at,
                turn_count=s.turn_count,
                metadata=s.metadata,
            )
            for s in summaries
        ]
    )


@router.get("/api/sessions/{session_id}", response_model=SessionHistoryResponse)
async def get_session_history(
    session_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    app=Depends(get_application),
) -> SessionHistoryResponse:
    if app.memory_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Memory store not initialized",
        )
    records = await app.memory_store.get_recent(session_id, limit=limit)
    return SessionHistoryResponse(
        session_id=session_id,
        turns=[
            TurnResponse(
                turn_id=r.turn_id,
                role=r.turn.role,
                content=r.turn.content,
                source=r.turn.source,
                created_at=r.turn.created_at.isoformat() if r.turn.created_at else "",
            )
            for r in records
        ],
    )
