"""FastAPI WebSocket endpoint for the Gateway-Node protocol.

The endpoint is a thin coordinator: it owns the socket read/write loop and
wires together the transport-agnostic protocol pieces (``NodeDispatcher``,
``NodeSession``) with the gateway services (``NodeRegistry``, ``NodeInvoker``,
``NodeAuthService``).

Lifecycle per connection:

1. Extract token from query params or ``Sec-WebSocket-Protocol``.
2. Verify token (fail closed with 1008 if invalid).
3. Create ``NodeSession`` in ``authenticating`` state and a ``NodeDispatcher``.
4. Register inbound handlers (``node.register``, ``node.input.submit``, ...).
5. Enter the read/write loop: parse frames, dispatch, send results.
6. Drive heartbeats on a timer.
7. On disconnect: mark offline, cancel pending requests, drop from registry.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from nahida_bot.gateway.node_protocol.auth import (
    evaluate_token,
    extract_token_from_query,
    extract_token_from_subprotocol,
)
from nahida_bot.gateway.node_protocol.dispatcher import (
    InboundHandlerResult,
    NodeDispatcher,
)
from nahida_bot.gateway.node_protocol.schemas import (
    NodeCapability,
    NodeEnvelope,
    HeartbeatPayload,
    NodeRegisterOkPayload,
    NodeRegisterPayload,
    PROTOCOL_VERSION,
    build_heartbeat,
)
from nahida_bot.gateway.node_protocol.sessions import (
    NodeSession,
    NodeSessionState,
)

if TYPE_CHECKING:
    from nahida_bot.gateway.services.node_invoker import NodeInvoker
    from nahida_bot.gateway.services.node_registry import NodeRegistry
    from nahida_bot.gateway.services.node_auth import NodeAuthService

logger = structlog.get_logger(__name__)

WS_CLOSE_POLICY_VIOLATION = 1008
WS_CLOSE_NORMAL = 1000
WS_CLOSE_UNSUPPORTED = 1003
WS_CLOSE_HEARTBEAT_TIMEOUT = 4000

router = APIRouter()


def _parse_frame(data: str | bytes) -> NodeEnvelope | None:
    try:
        raw = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    try:
        return NodeEnvelope.model_validate(raw)
    except Exception:  # noqa: BLE001 - malformed frame, just drop it
        return None


def _serialize(envelope: NodeEnvelope) -> str:
    return envelope.model_dump_json(exclude_none=True)


async def _send_envelope(ws: WebSocket, envelope: NodeEnvelope) -> bool:
    try:
        await ws.send_text(_serialize(envelope))
        return True
    except Exception:  # noqa: BLE001 - socket already closing
        return False


def register_inbound_handlers(
    dispatcher: NodeDispatcher,
    *,
    session: NodeSession,
    registry: NodeRegistry,
    invoker: NodeInvoker | None,
    auth_principal_node_id: str,
) -> None:
    """Wire protocol handlers that run inside the gateway process.

    These handlers implement gateway-side semantics for node->gateway requests.
    The ``capability.invoke`` direction (gateway->node) is owned by
    ``NodeInvoker`` and does not go through these handlers.
    """

    async def handle_register(
        sess: NodeSession, envelope: NodeEnvelope
    ) -> InboundHandlerResult | None:
        payload = envelope.typed_request_payload(NodeRegisterPayload)
        if payload.node_id != auth_principal_node_id:
            return InboundHandlerResult(
                ok=False,
                error=__err(
                    "register_rejected",
                    "node_id does not match the authenticated token",
                ),
            )

        # Capability names must be unique within a registration.
        seen: set[str] = set()
        clean_caps: list[NodeCapability] = []
        for cap in payload.capabilities:
            if cap.name in seen:
                continue
            seen.add(cap.name)
            clean_caps.append(cap)

        session_id = registry.register_session(
            session=sess,
            node_id=payload.node_id,
            display_name=payload.display_name or payload.node_id,
            node_type=payload.node_type,
            capabilities=clean_caps,
            metadata=payload.metadata,
        )
        ok_payload = NodeRegisterOkPayload(
            accepted=True,
            session_id=session_id,
            heartbeat_interval_ms=registry.heartbeat_interval_ms,
            heartbeat_timeout_ms=registry.heartbeat_timeout_ms,
        )
        logger.info(
            "node_protocol.registered",
            node_id=payload.node_id,
            session_id=session_id,
            capabilities=len(clean_caps),
        )
        return InboundHandlerResult(
            ok=True, payload=ok_payload.model_dump(mode="json", exclude_none=True)
        )

    async def handle_input_submit(
        sess: NodeSession, envelope: NodeEnvelope
    ) -> InboundHandlerResult | None:
        if invoker is None:
            return InboundHandlerResult(
                ok=False,
                error=__err("method_not_found", "input submission not enabled"),
            )
        from nahida_bot.gateway.node_protocol.schemas import (
            NodeInputSubmitPayload,
        )

        payload = envelope.typed_request_payload(NodeInputSubmitPayload)
        await invoker.submit_node_input(
            node_id=sess.node_id,
            credential_id=sess.credential_id,
            actor_account_key=sess.actor_account_key,
            conversation_id=sess.conversation_id or payload.session_id,
            text=payload.text,
        )
        return InboundHandlerResult(ok=True, payload={"accepted": True})

    dispatcher.register_inbound_handler("node.register", handle_register)
    dispatcher.register_inbound_handler("node.input.submit", handle_input_submit)


def __err(code: str, message: str) -> Any:
    from nahida_bot.gateway.node_protocol.schemas import NodeErrorObject

    return NodeErrorObject(code=code, message=message)


@router.websocket("/api/nodes/ws")
async def node_websocket(websocket: WebSocket) -> None:
    """Gateway-Node WebSocket endpoint."""
    app = websocket.app
    registry: NodeRegistry | None = getattr(app.state, "node_registry", None)
    auth_service: NodeAuthService | None = getattr(app.state, "node_auth", None)
    invoker: NodeInvoker | None = getattr(app.state, "node_invoker", None)

    if registry is None:
        await websocket.close(
            code=WS_CLOSE_UNSUPPORTED, reason="node protocol disabled"
        )
        return

    # -- Handshake auth ----------------------------------------------------
    # Query ?token=... is the primary path (browser/WebView friendly);
    # Sec-WebSocket-Protocol "nahida-node.<token>" is a fallback.
    token = extract_token_from_query(dict(websocket.query_params)) or (
        extract_token_from_subprotocol(
            websocket.headers.get("sec-websocket-protocol", "").split(", ")
        )
    )
    decision = evaluate_token(auth_service, token)

    if not decision.accepted:
        logger.warning("node_protocol.auth_rejected", reason=decision.reason)
        await websocket.close(code=WS_CLOSE_POLICY_VIOLATION, reason=decision.reason)
        return

    await websocket.accept()
    principal = decision.principal
    assert principal is not None  # accepted implies principal present

    session = NodeSession(
        session_id=f"node_session_{principal.token_id[:16]}",
        node_id=principal.node_id,
        credential_id=principal.token_id,
        actor_account_key=principal.actor_account_key,
        conversation_id=principal.conversation_id,
    )
    session.state = NodeSessionState.REGISTERING
    dispatcher = NodeDispatcher(session)

    # Wire transport-backed callbacks so the invoker/registry can send without
    # knowing about the socket or dispatcher.
    session.send = lambda env: _send_envelope(websocket, env)  # type: ignore[assignment]
    session.close = lambda code, reason: websocket.close(code=code, reason=reason)
    session.request = lambda env, timeout: _request_round_trip(  # type: ignore[assignment]
        websocket, dispatcher, env, timeout
    )

    register_inbound_handlers(
        dispatcher,
        session=session,
        registry=registry,
        invoker=invoker,
        auth_principal_node_id=principal.node_id,
    )

    heartbeat_task = asyncio.create_task(
        _heartbeat_loop(
            websocket,
            session,
            registry.heartbeat_interval_ms,
            registry.heartbeat_timeout_ms,
        )
    )

    logger.info("node_protocol.connected", node_id=session.node_id)
    try:
        await _read_loop(websocket, dispatcher, session)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("node_protocol.read_loop_failed", node_id=session.node_id)
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except (asyncio.CancelledError, Exception):
            pass
        dispatcher.cancel_all_pending(reason="session closed")
        registry.mark_offline(session)
        logger.info("node_protocol.disconnected", node_id=session.node_id)


async def _read_loop(
    websocket: WebSocket, dispatcher: NodeDispatcher, session: NodeSession
) -> None:
    while True:
        raw = await websocket.receive_text()
        envelope = _parse_frame(raw)
        if envelope is None:
            logger.warning("node_protocol.malformed_frame", node_id=session.node_id)
            continue
        if envelope.version != PROTOCOL_VERSION:
            # Forward-compatible: tolerate minor version drift, log only.
            logger.debug(
                "node_protocol.version_mismatch",
                node_id=session.node_id,
                version=envelope.version,
            )
        if envelope.kind == "heartbeat":
            await _handle_heartbeat(websocket, session, envelope)
            continue

        response = await dispatcher.handle_inbound(envelope)
        if response is not None:
            await _send_envelope(websocket, response)


async def _handle_heartbeat(
    websocket: WebSocket, session: NodeSession, envelope: NodeEnvelope
) -> None:
    try:
        payload = HeartbeatPayload.model_validate(envelope.payload or {})
    except Exception:  # noqa: BLE001 - malformed heartbeat, ignore frame
        logger.warning("node_protocol.invalid_heartbeat", node_id=session.node_id)
        return

    session.touch()
    if payload.type == "ping":
        await _send_envelope(
            websocket,
            build_heartbeat("pong", echo_ts=payload.ts),
        )


async def _heartbeat_loop(
    websocket: WebSocket, session: NodeSession, interval_ms: int, timeout_ms: int
) -> None:
    """Send periodic heartbeat pings; the transport layer expects a pong back."""
    interval = max(interval_ms / 1000.0, 1.0)
    timeout = max(timeout_ms / 1000.0, interval)
    try:
        while True:
            await asyncio.sleep(interval)
            elapsed = time.time() - session.last_seen_at.timestamp()
            if elapsed > timeout:
                logger.warning(
                    "node_protocol.heartbeat_timeout",
                    node_id=session.node_id,
                    elapsed=elapsed,
                    timeout=timeout,
                )
                await websocket.close(
                    code=WS_CLOSE_HEARTBEAT_TIMEOUT,
                    reason="heartbeat timeout",
                )
                return
            ping = build_heartbeat("ping", ts=int(time.time() * 1000))
            if not await _send_envelope(websocket, ping):
                return
    except asyncio.CancelledError:
        return


async def _request_round_trip(
    websocket: WebSocket,
    dispatcher: NodeDispatcher,
    envelope: NodeEnvelope,
    timeout: float,
) -> NodeEnvelope:
    """Send a request over the socket and await the matching response.

    Bridges the transport-agnostic dispatcher (which only tracks the pending
    future) with the WebSocket send path. The response is resolved when the
    node's reply frame arrives via ``_read_loop`` -> ``handle_inbound``.
    """
    future = dispatcher.register_pending(envelope)
    if not await _send_envelope(websocket, envelope):
        dispatcher.cancel_pending(envelope.id or "", reason="send failed")
        raise OSError("node socket send failed")
    return await dispatcher.wait_pending(
        request_id=envelope.id or "",
        future=future,
        timeout=timeout,
    )


__all__ = ["router", "node_websocket", "register_inbound_handlers"]
