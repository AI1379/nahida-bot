"""Pydantic models for the Gateway-Node wire protocol.

These models are the single source of truth for the Python side of the
protocol. They must be able to parse every fixture in
``tests/fixtures/gateway_node/*.json`` and round-trip to equivalent JSON.

Wire format uses ``snake_case``; unknown fields are ignored to keep the
protocol forward-compatible (see gateway-node-protocol.md §10.2).
"""

from __future__ import annotations

from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from nahida_bot.gateway.node_protocol.errors import NodeProtocolError

PROTOCOL_VERSION = "1.0"

_T = TypeVar("_T", bound=BaseModel)

Kind = Literal["request", "response", "event", "heartbeat"]
NodeDirection = Literal["gateway_to_node", "node_to_gateway", "bidirectional"]
NodeRisk = Literal["low", "medium", "high"]
NodeType = Literal["desktop", "worker", "tool-host"]


class _EnvelopeBase(BaseModel):
    model_config = ConfigDict(extra="ignore")


# -- Error -----------------------------------------------------------------


class NodeErrorObject(BaseModel):
    """Error object carried in a failed response."""

    model_config = ConfigDict(extra="ignore")

    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


# -- Capability ------------------------------------------------------------


class NodeCapability(BaseModel):
    """A capability declared by a node at registration time."""

    model_config = ConfigDict(extra="ignore")

    name: str
    version: str = "1.0"
    direction: NodeDirection = "gateway_to_node"
    risk: NodeRisk = "low"
    description: str = ""
    requires_user_approval: bool = False


# -- Register payloads -----------------------------------------------------


class NodeRegisterPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    node_id: str
    display_name: str = ""
    node_type: NodeType = "desktop"
    capabilities: list[NodeCapability] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class NodeRegisterOkPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    accepted: bool = True
    session_id: str
    heartbeat_interval_ms: int = 15000
    heartbeat_timeout_ms: int = 45000
    server_time: str = ""


# -- Capability invoke -----------------------------------------------------


class CapabilityInvokePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    invoke_id: str
    capability: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class CapabilityCancelPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    invoke_id: str


# -- Node input ------------------------------------------------------------


class NodeInputSubmitPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_id: str
    text: str


# -- Heartbeat -------------------------------------------------------------


class HeartbeatPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["ping", "pong"]
    ts: int | None = None
    echo_ts: int | None = None


# -- Envelope --------------------------------------------------------------

#: Discriminator union of all payload-bearing request methods. Each entry is a
#: ``(method, payload_model)`` pair resolved at dispatch time.
REQUEST_PAYLOAD_METHODS: dict[str, type[BaseModel]] = {
    "node.register": NodeRegisterPayload,
    "node.input.submit": NodeInputSubmitPayload,
    "capability.invoke": CapabilityInvokePayload,
    "capability.cancel": CapabilityCancelPayload,
}


class NodeEnvelope(_EnvelopeBase):
    """Unified envelope for all Gateway-Node messages."""

    version: str = PROTOCOL_VERSION
    kind: Kind
    id: str | None = None
    method: str | None = None
    event: str | None = None
    ok: bool | None = None
    payload: dict[str, Any] | None = None
    error: NodeErrorObject | None = None
    meta: dict[str, Any] = Field(default_factory=dict)

    def typed_request_payload(self, model: type[_T]) -> _T:
        """Validate ``payload`` against ``model`` and return the instance.

        Raises ``ValueError`` (which maps to ``invalid_arguments`` upstream)
        if the payload is missing or fails validation.
        """
        if self.payload is None:
            raise ValueError("request payload is required")
        return model.model_validate(self.payload)


def build_request(
    method: str,
    *,
    request_id: str,
    payload: dict[str, Any] | BaseModel | None = None,
) -> NodeEnvelope:
    """Build a request envelope ready to be serialized to JSON."""
    return NodeEnvelope(
        kind="request",
        id=request_id,
        method=method,
        payload=_coerce_payload(payload),
    )


def build_response(
    request_id: str,
    *,
    ok: bool,
    payload: dict[str, Any] | BaseModel | None = None,
    error: NodeErrorObject | None = None,
) -> NodeEnvelope:
    """Build a response envelope matching ``request_id``."""
    return NodeEnvelope(
        kind="response",
        id=request_id,
        ok=ok,
        payload=None if not ok else _coerce_payload(payload),
        error=error,
    )


def build_event(
    event: str,
    *,
    payload: dict[str, Any] | BaseModel | None = None,
) -> NodeEnvelope:
    """Build a one-way event envelope."""
    return NodeEnvelope(
        kind="event",
        event=event,
        payload=_coerce_payload(payload),
    )


def build_heartbeat(
    type_: Literal["ping", "pong"],
    *,
    ts: int | None = None,
    echo_ts: int | None = None,
) -> NodeEnvelope:
    payload: dict[str, Any] = {"type": type_}
    if ts is not None:
        payload["ts"] = ts
    if echo_ts is not None:
        payload["echo_ts"] = echo_ts
    return NodeEnvelope(kind="heartbeat", payload=payload)


def error_from_exception(exc: Exception) -> NodeErrorObject:
    """Map an exception to a NodeErrorObject using NodeProtocolError codes."""
    if isinstance(exc, NodeProtocolError):
        return NodeErrorObject(
            code=exc.code,
            message=str(exc) or exc.code,
            retryable=exc.retryable,
        )
    if isinstance(exc, ValidationError):
        return NodeErrorObject(
            code="invalid_arguments",
            message="payload schema validation failed",
            retryable=False,
            details={"errors": exc.errors()},
        )
    if isinstance(exc, ValueError):
        return NodeErrorObject(
            code="invalid_arguments",
            message=str(exc),
            retryable=False,
        )
    return NodeErrorObject(
        code="internal_error",
        message="Unexpected error",
        retryable=True,
    )


def _coerce_payload(
    payload: dict[str, Any] | BaseModel | None,
) -> dict[str, Any] | None:
    if payload is None:
        return None
    if isinstance(payload, BaseModel):
        return payload.model_dump(mode="json", exclude_none=True)
    return payload


__all__ = [
    "PROTOCOL_VERSION",
    "REQUEST_PAYLOAD_METHODS",
    "CapabilityCancelPayload",
    "CapabilityInvokePayload",
    "HeartbeatPayload",
    "NodeCapability",
    "NodeEnvelope",
    "NodeErrorObject",
    "NodeInputSubmitPayload",
    "NodeProtocolError",
    "NodeRegisterOkPayload",
    "NodeRegisterPayload",
    "build_event",
    "build_heartbeat",
    "build_request",
    "build_response",
    "error_from_exception",
]
