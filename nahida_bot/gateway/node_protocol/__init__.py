"""Gateway-Node wire protocol: language-agnostic JSON-over-WebSocket layer.

See ``docs/architecture/gateway-node-protocol.md`` for the protocol specification.
This package is transport-agnostic; the WebSocket endpoint lives in
``routes.py`` and the persistence-backed services live in
``nahida_bot.gateway.services``.
"""

from nahida_bot.gateway.node_protocol.auth import (
    AuthDecision,
    NodePrincipal,
    NodeTokenVerifier,
    evaluate_token,
    extract_token_from_query,
    extract_token_from_subprotocol,
)
from nahida_bot.gateway.node_protocol.dispatcher import (
    InboundHandler,
    InboundHandlerResult,
    NodeDispatcher,
    PendingRequest,
)
from nahida_bot.gateway.node_protocol.errors import (
    AuthFailed,
    AuthRequired,
    CapabilityCancelled,
    CapabilityDenied,
    CapabilityFailed,
    CapabilityLocalDenied,
    CapabilityNotFound,
    CapabilityTimeout,
    InternalError,
    InvalidArguments,
    MethodNotFound,
    NodeInputUnavailable,
    NodeProtocolError,
    NotRegistered,
    RateLimited,
    RegisterRejected,
    UnknownRequestId,
)
from nahida_bot.gateway.node_protocol.schemas import (
    PROTOCOL_VERSION,
    REQUEST_PAYLOAD_METHODS,
    CapabilityCancelPayload,
    CapabilityInvokePayload,
    HeartbeatPayload,
    NodeCapability,
    NodeEnvelope,
    NodeErrorObject,
    NodeInputSubmitPayload,
    NodeRegisterOkPayload,
    NodeRegisterPayload,
    build_event,
    build_heartbeat,
    build_request,
    build_response,
    error_from_exception,
)
from nahida_bot.gateway.node_protocol.sessions import (
    NodeSession,
    NodeSessionState,
)

__all__ = [
    # auth
    "AuthDecision",
    "NodePrincipal",
    "NodeTokenVerifier",
    "evaluate_token",
    "extract_token_from_query",
    "extract_token_from_subprotocol",
    # dispatcher
    "InboundHandler",
    "InboundHandlerResult",
    "NodeDispatcher",
    "PendingRequest",
    # errors
    "AuthFailed",
    "AuthRequired",
    "CapabilityCancelled",
    "CapabilityDenied",
    "CapabilityFailed",
    "CapabilityLocalDenied",
    "CapabilityNotFound",
    "CapabilityTimeout",
    "InternalError",
    "InvalidArguments",
    "MethodNotFound",
    "NodeInputUnavailable",
    "NodeProtocolError",
    "NotRegistered",
    "RateLimited",
    "RegisterRejected",
    "UnknownRequestId",
    # schemas
    "PROTOCOL_VERSION",
    "REQUEST_PAYLOAD_METHODS",
    "CapabilityCancelPayload",
    "CapabilityInvokePayload",
    "HeartbeatPayload",
    "NodeCapability",
    "NodeEnvelope",
    "NodeErrorObject",
    "NodeInputSubmitPayload",
    "NodeRegisterOkPayload",
    "NodeRegisterPayload",
    "build_event",
    "build_heartbeat",
    "build_request",
    "build_response",
    "error_from_exception",
    # sessions
    "NodeSession",
    "NodeSessionState",
]
