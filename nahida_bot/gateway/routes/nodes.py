"""REST endpoints for node management: listing, status, pairing and revocation.

These are HTTP companions to the WebSocket endpoint in
``nahida_bot.gateway.node_protocol.routes``. They let the WebUI inspect
online nodes and run the pairing flow. Node tokens themselves are issued via
``NodeAuthService``; this surface exposes that capability to authenticated
WebUI/operator callers.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from nahida_bot.gateway.deps import get_application

router = APIRouter()


class PairingStartRequest(BaseModel):
    node_id: str = Field(..., description="Proposed node identifier")
    display_name: str = ""
    scope: list[str] = Field(default_factory=list)


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
    pairing_token, token_id = auth.issue_pairing_token(
        node_id=body.node_id,
        display_name=body.display_name,
        scope=tuple(body.scope),
    )
    return PairingStartResponse(
        pairing_token=pairing_token,
        token_id=token_id,
        expires_in_seconds=auth._pairing_ttl,
        node_id=body.node_id,
    )


@router.post("/api/nodes/pairing/complete", response_model=PairingCompleteResponse)
async def pairing_complete(
    body: PairingCompleteRequest, request: Request, app=Depends(get_application)
) -> PairingCompleteResponse:
    _registry, auth, _invoker = _get_services(request)
    result = auth.exchange_pairing_for_node_token(body.pairing_token)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="pairing token is invalid, expired or already used",
        )
    node_token, token_id = result
    # node_id recovered from the (now-consumed) pairing principal.
    record = auth.store.get(token_id)
    node_id = record.node_id if record is not None else ""
    return PairingCompleteResponse(
        node_token=node_token,
        token_id=token_id,
        node_id=node_id,
    )


@router.post("/api/nodes/{node_id}/revoke", response_model=RevokeResponse)
async def revoke_node(
    node_id: str, request: Request, app=Depends(get_application)
) -> RevokeResponse:
    _registry, auth, _invoker = _get_services(request)
    count = auth.revoke_all_for_node(node_id)
    disconnected = await _registry.disconnect_node(
        node_id,
        reason="node token revoked",
    )
    return RevokeResponse(revoked=count > 0 or disconnected, token_id=node_id)


__all__ = ["router"]
