"""Capability invocation and node input submission.

The invoker is the gateway-side entry point for two gateway->node flows:

1. ``capability.invoke`` — find the online node owning a capability, send the
   request over its WebSocket, await the response (with timeout), and record
   an audit entry.
2. ``submit_node_input`` — forward a node-side user message into a session so
   the Agent Loop processes it. Marked ``source=node`` for auditability.

The invoker depends on the ``NodeRegistry`` (to locate nodes) and on the
agent-side session runner hook (to inject input). It never touches the wire
format directly: it builds ``NodeEnvelope`` requests and hands them to the
node's send callback via the dispatcher.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog

from nahida_bot.gateway.node_protocol.errors import (
    CapabilityDenied,
    CapabilityNotFound,
    CapabilityTimeout,
    NodeInputUnavailable,
)
from nahida_bot.gateway.node_protocol.schemas import (
    CapabilityInvokePayload,
    NodeErrorObject,
    build_request,
)
from nahida_bot.gateway.services.node_registry import NodeRegistry

logger = structlog.get_logger(__name__)

#: Default per-invocation timeout (seconds). Capabilities that legitimately
#: take longer (e.g. long TTS) should pass an explicit ``timeout``.
DEFAULT_INVOKE_TIMEOUT = 10.0


class NodeInputSink(Protocol):
    """Hook that turns a node-originated message into an agent run.

    Implemented by the session layer; the invoker stays decoupled from the
    Agent/Session internals.
    """

    async def submit(self, *, node_id: str, session_id: str, text: str) -> None: ...


@dataclass
class InvokeAuditEntry:
    invoke_id: str
    node_id: str
    capability: str
    arguments: dict[str, Any]
    caller: str
    started_at: float
    finished_at: float = 0.0
    ok: bool = False
    error_code: str = ""
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "invoke_id": self.invoke_id,
            "node_id": self.node_id,
            "capability": self.capability,
            "caller": self.caller,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "ok": self.ok,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


@dataclass
class InvokeResult:
    ok: bool
    payload: dict[str, Any] = field(default_factory=dict)
    error: NodeErrorObject | None = None
    audit: InvokeAuditEntry | None = None


class NodeInvoker:
    """Executes ``capability.invoke`` and ``node.input.submit`` flows."""

    def __init__(
        self,
        registry: NodeRegistry,
        *,
        input_sink: NodeInputSink | None = None,
        default_timeout: float = DEFAULT_INVOKE_TIMEOUT,
        audit_log: list[InvokeAuditEntry] | None = None,
    ) -> None:
        self._registry = registry
        self._input_sink = input_sink
        self._default_timeout = default_timeout
        self._audit_log: list[InvokeAuditEntry] = (
            audit_log if audit_log is not None else []
        )

    # -- Capability invocation --------------------------------------------

    async def invoke(
        self,
        *,
        capability: str,
        arguments: dict[str, Any] | None = None,
        caller: str = "system",
        node_id: str | None = None,
        timeout: float | None = None,
    ) -> InvokeResult:
        """Invoke a capability on an online node and await the response."""
        target = self._resolve_target(capability, node_id)
        if target is None:
            err = _err_from(CapabilityNotFound(f"no online node for {capability}"))
            return InvokeResult(ok=False, error=err)
        if target.get_capability(capability) is None:
            err = _err_from(CapabilityNotFound(f"node does not own {capability}"))
            return InvokeResult(ok=False, error=err)

        # Authorization hook: default policy allows everything the owner/system
        # requests; plug in a stricter policy by subclassing/overriding.
        if not self._authorize(caller=caller, capability=capability, node=target):
            err = _err_from(
                CapabilityDenied(f"{caller} not authorized for {capability}")
            )
            return InvokeResult(ok=False, error=err)

        invoke_id = f"inv_{secrets.token_urlsafe(9)}"
        request = build_request(
            "capability.invoke",
            request_id=f"req_inv_{secrets.token_hex(6)}",
            payload=CapabilityInvokePayload(
                invoke_id=invoke_id,
                capability=capability,
                arguments=arguments or {},
            ),
        )

        audit = InvokeAuditEntry(
            invoke_id=invoke_id,
            node_id=target.node_id,
            capability=capability,
            arguments=_summarize_arguments(arguments or {}),
            caller=caller,
            started_at=time.time(),
        )

        if target.request is None:
            err = _err_from(CapabilityNotFound("node session has no request channel"))
            return InvokeResult(ok=False, error=err)

        effective_timeout = self._default_timeout if timeout is None else timeout

        try:
            response = await asyncio.wait_for(
                target.request(request, effective_timeout),
                timeout=effective_timeout,
            )
        except TimeoutError:
            audit.finished_at = time.time()
            audit.ok = False
            audit.error_code = "capability_timeout"
            self._audit_log.append(audit)
            err = _err_from(CapabilityTimeout("node did not respond in time"))
            return InvokeResult(ok=False, error=err, audit=audit)
        except Exception as exc:  # noqa: BLE001 - transport failure mid-invoke
            audit.finished_at = time.time()
            audit.ok = False
            audit.error_code = "capability_failed"
            audit.error_message = str(exc)
            self._audit_log.append(audit)
            err = _err_from(CapabilityNotFound("node request channel failed"))
            return InvokeResult(ok=False, error=err, audit=audit)

        audit.finished_at = time.time()
        audit.ok = bool(response.ok)
        if response.error is not None:
            audit.error_code = response.error.code
            audit.error_message = response.error.message
        self._audit_log.append(audit)

        return InvokeResult(
            ok=bool(response.ok),
            payload=response.payload or {},
            error=response.error,
            audit=audit,
        )

    def _resolve_target(self, capability: str, node_id: str | None):
        if node_id is not None:
            return self._registry.get_online_session(node_id)
        return self._registry.find_capability_owner(capability)

    # -- Node input submission --------------------------------------------

    async def submit_node_input(
        self, *, node_id: str, session_id: str, text: str
    ) -> None:
        if self._input_sink is None:
            raise NodeInputUnavailable("node input submission is not configured")
        await self._input_sink.submit(node_id=node_id, session_id=session_id, text=text)

    # -- Authorization / audit --------------------------------------------

    def _authorize(self, *, caller: str, capability: str, node) -> bool:
        """Default permissive policy. Override for stricter control."""
        return True

    @property
    def audit_log(self) -> list[InvokeAuditEntry]:
        return self._audit_log


# -- Helpers ---------------------------------------------------------------


def _err_from(exc: Exception) -> NodeErrorObject:
    from nahida_bot.gateway.node_protocol.schemas import error_from_exception

    return error_from_exception(exc)


def _summarize_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Best-effort redaction of argument payloads for audit logs."""
    summary: dict[str, Any] = {}
    for key, value in arguments.items():
        if isinstance(value, str) and len(value) > 200:
            summary[key] = f"<str len={len(value)}>"
        elif isinstance(value, (bytes, bytearray)):
            summary[key] = f"<bytes len={len(value)}>"
        else:
            summary[key] = value
    return summary


__all__ = [
    "DEFAULT_INVOKE_TIMEOUT",
    "InvokeAuditEntry",
    "InvokeResult",
    "NodeInputSink",
    "NodeInvoker",
]
