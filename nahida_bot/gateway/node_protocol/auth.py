"""Protocol-level authentication types for node connections.

This module defines the data the protocol layer needs from auth. The actual
token storage, signing and pairing flow live in
``nahida_bot.gateway.services.node_auth`` to keep the protocol package free of
persistence concerns.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class NodePrincipal:
    """The validated identity attached to an authenticated node connection."""

    node_id: str
    token_id: str
    token_type: str = "node"  # "node" or "pairing"
    expires_at: str | None = None
    scope: tuple[str, ...] = ()
    # The credential authenticates a device/service.  It may explicitly act
    # for an account, but the node id itself is never an account identity.
    actor_account_key: str = ""
    conversation_id: str = ""


class NodeTokenVerifier(Protocol):
    """Interface implemented by the auth service.

    The WebSocket endpoint calls this during the handshake to decide whether
    to accept the connection and which ``node_id`` it belongs to.
    """

    def verify(self, token: str) -> NodePrincipal | None:
        """Return the principal for a valid token, or ``None`` if invalid."""
        ...


@dataclass
class AuthDecision:
    """Result of evaluating a presented token at the WebSocket handshake."""

    accepted: bool
    principal: NodePrincipal | None = None
    reason: str = ""


def evaluate_token(
    verifier: NodeTokenVerifier | None, token: str | None
) -> AuthDecision:
    """Evaluate a token extracted from the WebSocket handshake.

    A missing verifier or token yields an explicit rejection rather than a
    silent pass, so misconfiguration fails closed.
    """
    if verifier is None:
        return AuthDecision(accepted=False, reason="node auth not configured")
    if not token:
        return AuthDecision(accepted=False, reason="missing token")
    principal = verifier.verify(token)
    if principal is None:
        return AuthDecision(accepted=False, reason="invalid token")
    return AuthDecision(accepted=True, principal=principal)


def extract_token_from_query(query_params: dict[str, str]) -> str | None:
    """Extract the node token from WebSocket query parameters."""
    return query_params.get("token") or query_params.get("node_token")


def extract_token_from_subprotocol(
    subprotocols: list[str] | None,
) -> str | None:
    """Extract a node token passed via ``Sec-WebSocket-Protocol``.

    Convention: ``nahida-node.<token>``. Allows browsers/WebViews that cannot
    set Authorization headers to still authenticate the upgrade.
    """
    if not subprotocols:
        return None
    for proto in subprotocols:
        marker = "nahida-node."
        if proto.startswith(marker):
            return proto[len(marker) :]
    return None


__all__ = [
    "AuthDecision",
    "NodePrincipal",
    "NodeTokenVerifier",
    "evaluate_token",
    "extract_token_from_query",
    "extract_token_from_subprotocol",
]
