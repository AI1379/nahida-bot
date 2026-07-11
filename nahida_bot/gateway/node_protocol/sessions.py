"""Node session state machine and per-connection capability registry.

A ``NodeSession`` represents a single WebSocket connection. Sessions move
through ``authenticating -> registering -> online -> offline`` (see
gateway-node-protocol.md §5.6). Capability lookups are resolved against the
session's registered capabilities; cross-session aggregation lives in the
``NodeRegistry`` service.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING

from nahida_bot.gateway.node_protocol.schemas import NodeCapability

if TYPE_CHECKING:
    from nahida_bot.gateway.node_protocol.schemas import NodeEnvelope


class NodeSessionState(str, Enum):
    AUTHENTICATING = "authenticating"
    REGISTERING = "registering"
    ONLINE = "online"
    OFFLINE = "offline"


# A request round-trip: send the envelope over the wire, await the matching
# response via the connection's dispatcher. Set on the session by the
# WebSocket endpoint so the invoker stays transport-agnostic.
RequestCallable = Callable[["NodeEnvelope", float], Awaitable["NodeEnvelope"]]

# Raw one-way send (events, displace notifications). Fire-and-forget.
SendCallable = Callable[["NodeEnvelope"], Awaitable[None]]

# Transport close callback used when a session is displaced or revoked.
CloseCallable = Callable[[int, str], Awaitable[None]]


@dataclass
class NodeSession:
    """Tracks a single node WebSocket connection."""

    session_id: str
    node_id: str
    node_type: str = "desktop"
    display_name: str = ""
    state: NodeSessionState = NodeSessionState.AUTHENTICATING
    capabilities: list[NodeCapability] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)
    connected_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_seen_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    credential_id: str = ""
    actor_account_key: str = ""
    conversation_id: str = ""
    # Wired by the WebSocket endpoint (transport layer). ``request`` performs a
    # full round-trip; ``send`` is one-way. Both are optional so the session
    # can be constructed in tests without a live socket.
    request: RequestCallable | None = None
    send: SendCallable | None = None
    close: CloseCallable | None = None

    @property
    def capability_names(self) -> set[str]:
        return {cap.name for cap in self.capabilities}

    def get_capability(self, name: str) -> NodeCapability | None:
        for cap in self.capabilities:
            if cap.name == name:
                return cap
        return None

    def touch(self) -> None:
        self.last_seen_at = datetime.now(UTC)

    def mark_online(
        self,
        *,
        node_id: str,
        display_name: str,
        node_type: str,
        capabilities: list[NodeCapability],
        metadata: dict[str, object],
    ) -> None:
        self.node_id = node_id
        self.display_name = display_name
        self.node_type = node_type
        self.capabilities = list(capabilities)
        self.metadata = dict(metadata)
        self.state = NodeSessionState.ONLINE
        self.touch()

    def mark_offline(self) -> None:
        self.state = NodeSessionState.OFFLINE
        self.touch()

    def to_summary(self) -> dict[str, object]:
        """Return a JSON-friendly summary suitable for REST exposure."""
        return {
            "session_id": self.session_id,
            "node_id": self.node_id,
            "node_type": self.node_type,
            "display_name": self.display_name,
            "state": self.state.value,
            "online": self.state == NodeSessionState.ONLINE,
            "capabilities": [
                {
                    "name": cap.name,
                    "version": cap.version,
                    "direction": cap.direction,
                    "risk": cap.risk,
                    "description": cap.description,
                }
                for cap in self.capabilities
            ],
            "metadata": self.metadata,
            "connected_at": self.connected_at.isoformat(),
            "last_seen_at": self.last_seen_at.isoformat(),
            "actor_account_key": self.actor_account_key,
            "conversation_id": self.conversation_id,
        }


__all__ = [
    "CloseCallable",
    "NodeSession",
    "NodeSessionState",
    "RequestCallable",
    "SendCallable",
]
