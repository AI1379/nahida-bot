"""Session listing and history endpoints."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status

from nahida_bot.core.chat_address import classify_session_key
from nahida_bot.core.sentinel import detect_sentinel
from nahida_bot.gateway.deps import get_application
from nahida_bot.gateway.schemas import (
    MessageDeliveriesResponse,
    MessageDeliveryGroupResponse,
    MessageDeliveryGroupsResponse,
    MessageDeliveryResponse,
    SessionHistoryResponse,
    SessionListResponse,
    SessionSearchResponse,
    SessionSearchResultResponse,
    SessionSummaryResponse,
    TurnResponse,
)

logger = structlog.get_logger(__name__)

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
                session_key_kind=classify_session_key(s.session_id),
                workspace_id=s.workspace_id,
                created_at=s.created_at,
                last_active_at=s.last_active_at,
                turn_count=s.turn_count,
                metadata=s.metadata,
            )
            for s in summaries
        ]
    )


@router.get("/api/sessions/search", response_model=SessionSearchResponse)
async def search_sessions(
    q: str = Query(default=""),
    chat_address: str = Query(default=""),
    source: str = Query(default=""),
    role: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=500),
    app=Depends(get_application),
) -> SessionSearchResponse:
    results: list[SessionSearchResultResponse] = []
    role_filter = role.strip()
    include_turns = role_filter not in {"delivery", "message_delivery"}
    include_deliveries = role_filter in {"", "delivery", "message_delivery"}

    if include_turns:
        if app.memory_store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Memory store not initialized",
            )
        search_turns = getattr(app.memory_store, "search_turns", None)
        if not callable(search_turns):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Memory store does not support global search",
            )
        records = await search_turns(
            q,
            chat_address=chat_address,
            source=source,
            role=role_filter,
            limit=limit,
        )
        results.extend(_turn_search_result(record) for record in records)

    delivery_store = getattr(app, "message_delivery_store", None)
    if include_deliveries and delivery_store is not None:
        deliveries = await delivery_store.search(
            q,
            target_chat_address=chat_address,
            source=source,
            limit=limit,
        )
        results.extend(_delivery_search_result(delivery) for delivery in deliveries)

    results.sort(key=lambda item: item.created_at, reverse=True)
    return SessionSearchResponse(results=results[:limit])


@router.get(
    "/api/sessions/delivery-groups", response_model=MessageDeliveryGroupsResponse
)
async def list_delivery_groups(
    limit: int = Query(default=200, ge=1, le=500),
    app=Depends(get_application),
) -> MessageDeliveryGroupsResponse:
    delivery_store = getattr(app, "message_delivery_store", None)
    if delivery_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Message delivery store not initialized",
        )
    groups = await delivery_store.list_groups(limit=limit)
    return MessageDeliveryGroupsResponse(
        groups=[
            MessageDeliveryGroupResponse(
                target_chat_address=group.target_chat_address,
                platform=group.platform,
                target_type=group.target_type,
                target_id=group.target_id,
                count=group.count,
                last_created_at=group.last_created_at,
                last_source=group.last_source,
            )
            for group in groups
        ]
    )


@router.get("/api/sessions/deliveries", response_model=MessageDeliveriesResponse)
async def list_deliveries(
    target: str = Query(...),
    limit: int = Query(default=200, ge=1, le=500),
    app=Depends(get_application),
) -> MessageDeliveriesResponse:
    delivery_store = getattr(app, "message_delivery_store", None)
    if delivery_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Message delivery store not initialized",
        )
    deliveries = await delivery_store.list_for_target(target, limit=limit)
    return MessageDeliveriesResponse(
        target_chat_address=target,
        deliveries=[_delivery_to_response(delivery) for delivery in deliveries],
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
        turns=[_turn_to_response(r) for r in records],
    )


def _turn_to_response(record) -> TurnResponse:
    sr = detect_sentinel(record.turn.content)
    return TurnResponse(
        turn_id=record.turn_id,
        role=record.turn.role,
        content=record.turn.content,
        source=record.turn.source,
        created_at=record.turn.created_at.isoformat() if record.turn.created_at else "",
        metadata=record.turn.metadata or {},
        sentinel_action=sr.action,
        sentinel_suppressed=bool(sr.action is not None and not sr.text),
    )


def _delivery_to_response(delivery) -> MessageDeliveryResponse:
    sentinel_action, sentinel_suppressed = _delivery_sentinel(delivery)
    return MessageDeliveryResponse(
        delivery_id=delivery.delivery_id,
        target_chat_address=delivery.target_chat_address,
        platform=delivery.platform,
        target_type=delivery.target_type,
        target_id=delivery.target_id,
        source_session_id=delivery.source_session_id,
        source_chat_address=delivery.source_chat_address,
        source_user_id=delivery.source_user_id,
        source=delivery.source,
        delivery_mode=delivery.delivery_mode,
        status=delivery.status,
        message_id=delivery.message_id,
        text=delivery.text,
        error=delivery.error,
        metadata=delivery.metadata,
        created_at=delivery.created_at,
        sentinel_action=sentinel_action,
        sentinel_suppressed=sentinel_suppressed,
    )


def _turn_search_result(record) -> SessionSearchResultResponse:
    response = _turn_to_response(record)
    return SessionSearchResultResponse(
        result_type="turn",
        id=str(response.turn_id),
        session_id=record.session_id,
        target_chat_address=_chat_address_from_session_id(record.session_id),
        role=response.role,
        source=response.source,
        content=response.content,
        created_at=response.created_at,
        metadata=response.metadata,
        sentinel_action=response.sentinel_action,
        sentinel_suppressed=response.sentinel_suppressed,
    )


def _delivery_search_result(delivery) -> SessionSearchResultResponse:
    response = _delivery_to_response(delivery)
    return SessionSearchResultResponse(
        result_type="delivery",
        id=response.delivery_id,
        session_id=response.source_session_id,
        target_chat_address=response.target_chat_address,
        role="delivery",
        source=response.source,
        content=response.text,
        created_at=response.created_at,
        metadata=response.metadata,
        sentinel_action=response.sentinel_action,
        sentinel_suppressed=response.sentinel_suppressed,
        delivery_mode=response.delivery_mode,
        status=response.status,
        message_id=response.message_id,
    )


def _delivery_sentinel(delivery) -> tuple[str | None, bool]:
    metadata = delivery.metadata if isinstance(delivery.metadata, dict) else {}
    raw_action = metadata.get("sentinel_action")
    if isinstance(raw_action, str) and raw_action:
        return raw_action, bool(metadata.get("sentinel_suppressed", False))
    sr = detect_sentinel(delivery.text)
    return sr.action, bool(sr.action is not None and not sr.text)


def _chat_address_from_session_id(session_id: str) -> str:
    parts = session_id.split(":")
    if len(parts) >= 3 and parts[1] in {
        "private",
        "group",
        "channel",
        "thread",
        "unknown",
    }:
        return ":".join(parts[:3])
    if len(parts) >= 2:
        return ":".join(parts[:2])
    return session_id
