"""Operator-only memory overview and evidence drill-down, without model calls."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Awaitable, Callable, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from nahida_bot.agent.memory.attribution import (
    EvidenceRef,
    MemoryAttribution,
    item_subject,
)
from nahida_bot.agent.memory.models import MemoryItem, MemoryRecord
from nahida_bot.agent.memory.portability import normalize_portable
from nahida_bot.gateway.deps import get_application
from nahida_bot.gateway.routes.memory import (
    MemoryItemResponse,
    MemoryTurnResponse,
    _item_response,
    _memory_service,
    _turn_response,
)

router = APIRouter()


class MemoryScopeSummary(BaseModel):
    scope_type: str
    scope_id: str
    active: int
    restricted: int
    last_updated_at: str


class MemoryOverviewResponse(BaseModel):
    total: int
    active: int
    archived: int
    restricted: int
    without_subject: int
    without_evidence: int
    last_updated_at: str = ""
    candidates: dict[str, int] = Field(default_factory=dict)
    scopes: list[MemoryScopeSummary] = Field(default_factory=list)
    scopes_truncated: bool = False
    soft_scope: bool = False


class MemoryEvidenceResponse(BaseModel):
    reference: dict[str, Any]
    turn: MemoryTurnResponse | None = None
    status: str
    excerpt_truncated: bool = False


class MemoryDetailResponse(BaseModel):
    item: MemoryItemResponse
    subject: dict[str, Any]
    portable: bool
    evidence: list[MemoryEvidenceResponse]
    evidence_truncated: bool = False
    replaces_item_id: str = ""


@router.get("/api/memory/overview", response_model=MemoryOverviewResponse)
async def memory_overview(
    scope_type: str = Query(default=""),
    scope_id: str = Query(default=""),
    app=Depends(get_application),
) -> MemoryOverviewResponse:
    service = _memory_service(app)
    inspect = cast(
        Callable[..., Awaitable[dict[str, Any]]] | None,
        getattr(service.store, "inspect_overview", None),
    )
    if not callable(inspect):
        raise HTTPException(status_code=503, detail="Memory overview is unavailable")
    summary = await inspect(scope_type.strip(), scope_id.strip())
    return MemoryOverviewResponse(**summary, soft_scope=service.soft_scope)


@router.get("/api/memory/items/{item_id}", response_model=MemoryDetailResponse)
async def memory_item_detail(
    item_id: str, app=Depends(get_application)
) -> MemoryDetailResponse:
    service = _memory_service(app)
    inspect = cast(
        Callable[[str], Awaitable[MemoryItem | None]] | None,
        getattr(service.store, "inspect_item", None),
    )
    if callable(inspect):
        item = await inspect(item_id)
    else:
        items = await service.store.get_items_by_ids([item_id])
        item = items[0] if items else None
    if item is None:
        raise HTTPException(status_code=404, detail="Memory item not found")
    refs = MemoryAttribution.parse(item.metadata).evidence
    records = {}
    read_turns = cast(
        Callable[[list[int]], Awaitable[list[MemoryRecord]]] | None,
        getattr(service.store, "get_turns_by_ids", None),
    )
    if callable(read_turns):
        records = {
            record.turn_id: record
            for record in await read_turns(
                list(dict.fromkeys(ref.turn_id for ref in refs[:50] if ref.turn_id > 0))
            )
        }
    evidence = []
    for ref in refs[:50]:
        record = records.get(ref.turn_id)
        if record is None and ref.turn_id == 0 and ref.message_id and ref.session_id:
            record = await service.find_turn_by_message_id(
                ref.message_id, session_id=ref.session_id, chat_address=ref.chat_id
            )
        evidence.append(_evidence_view(ref, record))
    return MemoryDetailResponse(
        item=_item_response(item),
        subject=asdict(item_subject(item)),
        portable=normalize_portable(item.metadata.get("portable")),
        evidence=evidence,
        evidence_truncated=len(refs) > 50,
        replaces_item_id=str(item.metadata.get("replaces_item_id") or ""),
    )


def _evidence_view(
    ref: EvidenceRef, record: MemoryRecord | None
) -> MemoryEvidenceResponse:
    state = "available" if record is not None else "missing"
    if record is not None:
        actual = EvidenceRef.from_record(record)
        if any(
            expected and expected != found
            for expected, found in (
                (ref.session_id, actual.session_id),
                (ref.chat_id, actual.chat_id),
                (ref.account_key, actual.account_key),
                (ref.message_id, actual.message_id),
                (ref.role, actual.role),
            )
        ):
            record, state = None, "mismatch"
    turn = _turn_response(record) if record is not None else None
    truncated = turn is not None and len(turn.content) > 12000
    if turn is not None:
        turn.content = turn.content[:12000]
    return MemoryEvidenceResponse(
        reference=asdict(ref), turn=turn, status=state, excerpt_truncated=truncated
    )
