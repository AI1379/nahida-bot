"""Error codes and exception tree for the Gateway-Node protocol.

Error codes are a stable contract: additions are allowed, but existing code
semantics must not change in a breaking way (see gateway-node-protocol.md §9).
"""

from __future__ import annotations


class NodeProtocolError(Exception):
    """Base class for all Gateway-Node protocol errors."""

    code: str = "internal_error"
    retryable: bool = False

    def __init__(self, message: str = "", *, retryable: bool | None = None) -> None:
        super().__init__(message)
        if retryable is not None:
            self.retryable = retryable


class AuthFailed(NodeProtocolError):
    code = "auth_failed"


class AuthRequired(NodeProtocolError):
    code = "auth_required"


class NotRegistered(NodeProtocolError):
    code = "not_registered"


class RegisterRejected(NodeProtocolError):
    code = "register_rejected"


class CapabilityNotFound(NodeProtocolError):
    code = "capability_not_found"


class CapabilityDenied(NodeProtocolError):
    code = "capability_denied"


class CapabilityLocalDenied(NodeProtocolError):
    code = "capability_local_denied"


class InvalidArguments(NodeProtocolError):
    code = "invalid_arguments"


class CapabilityFailed(NodeProtocolError):
    code = "capability_failed"
    retryable = True


class CapabilityTimeout(NodeProtocolError):
    code = "capability_timeout"
    retryable = True


class CapabilityCancelled(NodeProtocolError):
    code = "capability_cancelled"


class MethodNotFound(NodeProtocolError):
    code = "method_not_found"


class RateLimited(NodeProtocolError):
    code = "rate_limited"
    retryable = True


class InternalError(NodeProtocolError):
    code = "internal_error"
    retryable = True


class UnknownRequestId(NodeProtocolError):
    code = "unknown_request_id"
