"""REST endpoints for node management: listing, status, pairing and revocation.

These are HTTP companions to the WebSocket endpoint in
``nahida_bot.gateway.node_protocol.routes``. They let the WebUI inspect
online nodes and run the pairing flow. Node tokens themselves are issued via
``NodeAuthService``; this surface exposes that capability to authenticated
WebUI/operator callers.

The ``pairing/complete`` endpoint is split into ``public_router`` because the
pairing token is the credential: requiring WebUI admin auth on top of it would
defeat the whole point of the out-of-band pairing dance (admin mints a
pairing token, hands it to the desktop user, desktop exchanges it without
needing admin rights).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from nahida_bot.gateway.deps import get_application
from nahida_bot.identity.models import AccountKey

router = APIRouter()
public_router = APIRouter()


class PairingStartRequest(BaseModel):
    node_id: str = Field(..., description="Proposed node identifier")
    display_name: str = ""
    scope: list[str] = Field(default_factory=list)
    actor_account_key: str = Field(
        default="",
        description="Account this credential may act for; never inferred from node_id",
    )
    conversation_id: str = Field(
        default="",
        description="Optional server-approved default conversation/history lane",
    )


class PairingStartResponse(BaseModel):
    pairing_token: str
    token_id: str
    expires_in_seconds: int
    node_id: str


class PairingCompleteRequest(BaseModel):
    pairing_token: str = Field(..., description="One-shot pairing token to exchange")


class PairingCompleteResponse(BaseModel):
    node_token: str
    token_id: str
    node_id: str
    actor_account_key: str = ""
    conversation_id: str = ""


class NodeSummaryResponse(BaseModel):
    nodes: list[dict[str, Any]]


class NodeDetailResponse(BaseModel):
    node_id: str
    online: bool
    state: dict[str, Any] | None = None
    summary: dict[str, Any] = Field(default_factory=dict)


class RevokeResponse(BaseModel):
    revoked: bool
    token_id: str = ""


def _get_services(request: Request):
    """Resolve the node services from app state, raising 503 if disabled."""
    registry = getattr(request.app.state, "node_registry", None)
    auth = getattr(request.app.state, "node_auth", None)
    invoker = getattr(request.app.state, "node_invoker", None)
    if registry is None or auth is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="node protocol is not enabled",
        )
    return registry, auth, invoker


@router.get("/api/nodes", response_model=NodeSummaryResponse)
async def list_nodes(
    request: Request, app=Depends(get_application)
) -> NodeSummaryResponse:
    registry, _auth, _invoker = _get_services(request)
    return NodeSummaryResponse(nodes=registry.list_online_nodes())


@router.get("/api/nodes/{node_id}", response_model=NodeDetailResponse)
async def get_node(
    node_id: str, request: Request, app=Depends(get_application)
) -> NodeDetailResponse:
    registry, _auth, _invoker = _get_services(request)
    session = registry.get_online_session(node_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"node {node_id!r} is not online",
        )
    return NodeDetailResponse(
        node_id=node_id,
        online=True,
        summary=session.to_summary(),
        state=registry.get_state_summary(node_id),
    )


@router.post("/api/nodes/pairing/start", response_model=PairingStartResponse)
async def pairing_start(
    body: PairingStartRequest, request: Request, app=Depends(get_application)
) -> PairingStartResponse:
    _registry, auth, _invoker = _get_services(request)
    if body.actor_account_key:
        try:
            AccountKey.parse(body.actor_account_key)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
    pairing_token, token_id = await auth.issue_pairing_token(
        node_id=body.node_id,
        display_name=body.display_name,
        scope=tuple(body.scope),
        actor_account_key=body.actor_account_key,
        conversation_id=body.conversation_id,
    )
    return PairingStartResponse(
        pairing_token=pairing_token,
        token_id=token_id,
        expires_in_seconds=auth._pairing_ttl,
        node_id=body.node_id,
    )


@public_router.post(
    "/api/nodes/pairing/complete", response_model=PairingCompleteResponse
)
async def pairing_complete(
    body: PairingCompleteRequest, request: Request, app=Depends(get_application)
) -> PairingCompleteResponse:
    """Exchange a one-shot pairing token for a long-lived node token.

    Public endpoint: the pairing token itself is the credential. Admin auth
    is intentionally NOT required so the desktop user can complete pairing
    after an admin hands them a pairing token out-of-band.
    """
    _registry, auth, _invoker = _get_services(request)
    result = await auth.exchange_pairing_for_node_token(body.pairing_token)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="pairing token is invalid, expired or already used",
        )
    node_token, token_id = result
    # node_id recovered from the (now-consumed) pairing principal.
    record = await auth.store.get(token_id)
    node_id = record.node_id if record is not None else ""
    return PairingCompleteResponse(
        node_token=node_token,
        token_id=token_id,
        node_id=node_id,
        actor_account_key=(record.actor_account_key if record is not None else ""),
        conversation_id=(record.conversation_id if record is not None else ""),
    )


@router.post("/api/nodes/{node_id}/revoke", response_model=RevokeResponse)
async def revoke_node(
    node_id: str, request: Request, app=Depends(get_application)
) -> RevokeResponse:
    _registry, auth, _invoker = _get_services(request)
    count = await auth.revoke_all_for_node(node_id)
    disconnected = await _registry.disconnect_node(
        node_id,
        reason="node token revoked",
    )
    return RevokeResponse(revoked=count > 0 or disconnected, token_id=node_id)


__all__ = ["router", "public_router"]
