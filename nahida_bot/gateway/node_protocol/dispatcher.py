"""Message routing, response matching and event fanout for the node protocol.

The dispatcher is transport-agnostic: it consumes ``NodeEnvelope`` objects and
emits ``NodeEnvelope`` objects to send. The WebSocket endpoint (``routes.py``)
wires JSON frames to and from the dispatcher. This separation keeps the
protocol logic unit-testable without a live socket.

Two directions of requests are supported:

- **Inbound requests** (node -> gateway): methods like ``node.register`` and
  ``node.input.submit``. Resolved via registered ``InboundHandler`` callbacks.
- **Outbound requests** (gateway -> node): methods like ``capability.invoke``
  sent to a node. Tracked via pending-request futures so callers can ``await``
  the response.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import structlog

from nahida_bot.gateway.node_protocol.errors import (
    MethodNotFound,
    NotRegistered,
    RegisterRejected,
)
from nahida_bot.gateway.node_protocol.schemas import (
    NodeEnvelope,
    NodeErrorObject,
    REQUEST_PAYLOAD_METHODS,
    build_response,
    error_from_exception,
)
from nahida_bot.gateway.node_protocol.sessions import (
    NodeSession,
    NodeSessionState,
)

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)

#: Signature for handlers processing node->gateway requests.
InboundHandler = Callable[
    [NodeSession, NodeEnvelope], Awaitable["InboundHandlerResult | None"]
]


class InboundHandlerResult:
    """Optional structured result returned by an inbound handler.

    A handler may either:

    - return ``None`` to signal "I already sent the response myself", or
    - return this object so the dispatcher sends a uniform response envelope.
    """

    def __init__(
        self,
        *,
        ok: bool = True,
        payload: dict[str, Any] | None = None,
        error: NodeErrorObject | None = None,
    ) -> None:
        self.ok = ok
        self.payload = payload
        self.error = error


class PendingRequest:
    """Tracks an outbound request awaiting a node response."""

    __slots__ = ("request_id", "method", "future", "session_id")

    def __init__(self, request_id: str, method: str, session_id: str) -> None:
        self.request_id = request_id
        self.method = method
        self.session_id = session_id
        self.future: asyncio.Future[NodeEnvelope] = (
            asyncio.get_event_loop().create_future()
        )


class NodeDispatcher:
    """Routes envelopes for a single node connection.

    Each ``NodeSession`` owns one dispatcher. The dispatcher is **not**
    thread-safe; it must be driven from the asyncio event loop that owns the
    session.
    """

    def __init__(self, session: NodeSession) -> None:
        self._session = session
        self._inbound_handlers: dict[str, InboundHandler] = {}
        self._pending: dict[str, PendingRequest] = {}

    # -- Registration ------------------------------------------------------

    def register_inbound_handler(self, method: str, handler: InboundHandler) -> None:
        """Register a handler for an inbound (node->gateway) request method."""
        self._inbound_handlers[method] = handler

    def unregister_inbound_handler(self, method: str) -> None:
        self._inbound_handlers.pop(method, None)

    # -- Inbound: frames arriving from the node ----------------------------

    async def handle_inbound(self, envelope: NodeEnvelope) -> NodeEnvelope | None:
        """Process an envelope received from the node.

        Returns the envelope to send back to the node, or ``None`` if nothing
        should be sent (e.g. the handler took ownership of the response, or
        the frame was an event the dispatcher does not echo).
        """
        self._session.touch()

        if envelope.kind == "heartbeat":
            return None  # heartbeats handled at the transport layer

        if envelope.kind == "event":
            logger.debug(
                "node_dispatcher.inbound_event_ignored",
                node_id=self._session.node_id,
                event_name=envelope.event,
            )
            return None

        if envelope.kind == "response":
            return self._match_response(envelope)

        if envelope.kind == "request":
            return await self._handle_request(envelope)

        logger.warning(
            "node_dispatcher.unknown_kind",
            node_id=self._session.node_id,
            kind=envelope.kind,
        )
        return None

    async def _handle_request(self, envelope: NodeEnvelope) -> NodeEnvelope:
        request_id = envelope.id or ""
        method = envelope.method or ""

        if self._session.state == NodeSessionState.OFFLINE:
            err = error_from_exception(NotRegistered("session is offline"))
            return build_response(request_id, ok=False, error=err)

        # Unregistered sessions may only call node.register.
        if self._session.state != NodeSessionState.ONLINE and method != "node.register":
            err = error_from_exception(NotRegistered("must register first"))
            return build_response(request_id, ok=False, error=err)

        if self._session.state == NodeSessionState.ONLINE and method == "node.register":
            err = error_from_exception(RegisterRejected("node is already registered"))
            return build_response(request_id, ok=False, error=err)

        handler = self._inbound_handlers.get(method)
        if handler is None:
            err = error_from_exception(MethodNotFound(f"unknown method: {method}"))
            return build_response(request_id, ok=False, error=err)

        payload_model = REQUEST_PAYLOAD_METHODS.get(method)
        try:
            if payload_model is not None:
                envelope.typed_request_payload(payload_model)
        except Exception as exc:  # noqa: BLE001 - surfaced as protocol error
            err = error_from_exception(exc)
            return build_response(request_id, ok=False, error=err)

        try:
            result = await handler(self._session, envelope)
        except Exception as exc:  # noqa: BLE001 - surfaced as protocol error
            logger.exception(
                "node_dispatcher.handler_failed",
                node_id=self._session.node_id,
                method=method,
            )
            err = error_from_exception(exc)
            return build_response(request_id, ok=False, error=err)

        if result is None:
            # Handler owns the response; send nothing here.
            return build_response(request_id, ok=True, payload={})

        return build_response(
            request_id,
            ok=result.ok,
            payload=result.payload,
            error=result.error,
        )

    def _match_response(self, envelope: NodeEnvelope) -> NodeEnvelope | None:
        request_id = envelope.id or ""
        pending = self._pending.pop(request_id, None)
        if pending is None:
            logger.warning(
                "node_dispatcher.unknown_response_id",
                node_id=self._session.node_id,
                request_id=request_id,
            )
            return None
        if not pending.future.done():
            pending.future.set_result(envelope)
        return None

    # -- Outbound: gateway -> node ----------------------------------------

    def _new_request_id(self, method: str) -> str:
        prefix = method.split(".", 1)[0][:12]
        return f"req_{prefix}_{secrets.token_urlsafe(9)}"

    def has_pending(self, request_id: str) -> bool:
        return request_id in self._pending

    def register_pending(self, envelope: NodeEnvelope) -> asyncio.Future[NodeEnvelope]:
        """Synchronously register an outbound request before it is sent."""
        if envelope.kind != "request" or not envelope.id or not envelope.method:
            raise ValueError(
                "register_pending requires a request envelope with id+method"
            )
        if envelope.id in self._pending:
            raise ValueError(f"duplicate request id: {envelope.id}")
        pending = PendingRequest(
            envelope.id, envelope.method or "", self._session.session_id
        )
        self._pending[envelope.id] = pending
        return pending.future

    def cancel_pending(self, request_id: str, *, reason: str = "") -> bool:
        """Cancel a pending outbound request, resolving its future with an error."""
        pending = self._pending.pop(request_id, None)
        if pending is None:
            return False
        if not pending.future.done():
            err = NodeErrorObject(
                code="capability_cancelled",
                message=reason or "request cancelled",
                retryable=False,
            )
            pending.future.set_result(
                NodeEnvelope(
                    kind="response",
                    id=request_id,
                    ok=False,
                    error=err,
                )
            )
        return True

    def cancel_all_pending(self, *, reason: str = "session closed") -> None:
        for request_id in list(self._pending):
            self.cancel_pending(request_id, reason=reason)

    async def await_response(
        self, envelope: NodeEnvelope, *, timeout: float | None = None
    ) -> NodeEnvelope:
        """Send ``envelope`` (a request) and await the matching response.

        The caller is responsible for actually transmitting ``envelope`` to
        the node before or after calling this method; this helper only tracks
        the request/response correlation and resolves when the response frame
        arrives via ``handle_inbound``.
        """
        future = self.register_pending(envelope)
        request_id = envelope.id or ""
        return await self.wait_pending(
            request_id=request_id,
            future=future,
            timeout=timeout,
        )

    async def wait_pending(
        self,
        *,
        request_id: str,
        future: asyncio.Future[NodeEnvelope],
        timeout: float | None = None,
    ) -> NodeEnvelope:
        """Await a future created by ``register_pending`` and cleanup on timeout."""
        try:
            if timeout is not None:
                return await asyncio.wait_for(future, timeout=timeout)
            return await future
        except TimeoutError:
            pending = self._pending.get(request_id)
            if pending is not None and pending.future is future:
                self._pending.pop(request_id, None)
            raise


__all__ = [
    "InboundHandler",
    "InboundHandlerResult",
    "NodeDispatcher",
    "PendingRequest",
]
