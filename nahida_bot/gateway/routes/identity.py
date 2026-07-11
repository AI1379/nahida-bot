"""Authenticated identity management API (issue #7 Phase 4)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from nahida_bot.gateway.deps import get_application
from nahida_bot.identity.management import IdentityManagementError, IdentityManager

router = APIRouter(prefix="/api/identity", tags=["identity"])


class PersonMutation(BaseModel):
    person_id: str
    display_name: str = ""


class AccountLinkMutation(BaseModel):
    account_key: str
    person_id: str
    label: str = ""


class AccountUnlinkMutation(BaseModel):
    account_key: str


class IdentityListResponse(BaseModel):
    people: list[dict[str, object]] = Field(default_factory=list)
    observations: list[dict[str, object]] = Field(default_factory=list)
    audit: list[dict[str, object]] = Field(default_factory=list)


def _store(app):
    store = getattr(app, "_identity_store", None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="identity store is not initialized",
        )
    return store


@router.get("", response_model=IdentityListResponse)
async def list_identity(
    request: Request,
    account_key: str = "",
    limit: int = Query(default=100, ge=1, le=500),
    app=Depends(get_application),
) -> IdentityListResponse:
    store = _store(app)
    people = await store.list_people()
    person_rows: list[dict[str, object]] = []
    for person in people:
        accounts = await store.list_accounts(person.person_id)
        person_rows.append(
            {
                "person_id": person.person_id,
                "display_name": person.display_name,
                "status": person.status,
                "accounts": [
                    {
                        "account_key": item.account_key,
                        "label": item.label,
                        "verification": item.verification,
                        "linked_by": item.linked_by,
                    }
                    for item in accounts
                ],
            }
        )
    observations = await store.list_observations(
        account_key=account_key,
        limit=limit,
    )
    audit = await store.list_audit(limit=limit)
    return IdentityListResponse(
        people=person_rows,
        observations=[
            {
                "account_key": item.account_key,
                "chat_address": item.chat_address,
                "display_name": item.display_name,
                "role_tags": list(item.role_tags),
                "last_message_id": item.last_message_id,
                "first_seen_at": item.first_seen_at.isoformat(),
                "last_seen_at": item.last_seen_at.isoformat(),
            }
            for item in observations
        ],
        audit=[
            {
                "audit_id": item.audit_id or 0,
                "action": item.action,
                "actor": item.actor,
                "target_type": item.target_type,
                "target_id": item.target_id,
                "before": item.before,
                "after": item.after,
                "created_at": item.created_at.isoformat(),
            }
            for item in audit
        ],
    )


@router.post("/persons")
async def upsert_person(
    body: PersonMutation,
    request: Request,
    app=Depends(get_application),
) -> dict[str, object]:
    manager = IdentityManager(_store(app))
    try:
        person = await manager.create_or_update_person(
            person_id=body.person_id,
            display_name=body.display_name,
            actor="webapi:operator",
        )
    except IdentityManagementError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"person_id": person.person_id, "display_name": person.display_name}


@router.post("/accounts/link")
async def link_account(
    body: AccountLinkMutation,
    request: Request,
    app=Depends(get_application),
) -> dict[str, object]:
    manager = IdentityManager(_store(app))
    try:
        link = await manager.link_account(
            account_key=body.account_key,
            person_id=body.person_id,
            label=body.label,
            actor="webapi:operator",
        )
    except (IdentityManagementError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"account_key": link.account_key, "person_id": link.person_id}


@router.post("/accounts/unlink")
async def unlink_account(
    body: AccountUnlinkMutation,
    request: Request,
    app=Depends(get_application),
) -> dict[str, object]:
    manager = IdentityManager(_store(app))
    try:
        changed = await manager.unlink_account(
            account_key=body.account_key,
            actor="webapi:operator",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"account_key": body.account_key, "unlinked": changed}


__all__ = ["router"]
