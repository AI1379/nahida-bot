"""Online/offline node registry and session lifecycle.

The registry is the gateway-side bookkeeping for all node WebSocket sessions.
It owns the live ``NodeSession`` objects (keyed by ``session_id``) and indexes
them by ``node_id`` so capability invocation and REST queries can resolve a
target node.

Duplicate-connection policy (gateway-node-protocol.md §5.3): when a new
session registers with an ``node_id`` that already has an online session, the
old session is displaced (sent a ``node.duplicate_connection`` event and
marked offline). The new connection wins.
"""

from __future__ import annotations

import asyncio
import secrets
from typing import TYPE_CHECKING

import structlog

from nahida_bot.gateway.node_protocol.schemas import (
    NodeCapability,
    build_event,
)
from nahida_bot.gateway.node_protocol.sessions import (
    NodeSession,
    NodeSessionState,
)

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)


class NodeRegistry:
    """Tracks online node sessions and their capabilities."""

    def __init__(
        self,
        *,
        heartbeat_interval_ms: int = 15000,
        heartbeat_timeout_ms: int = 45000,
    ) -> None:
        self._by_session: dict[str, NodeSession] = {}
        self._by_node: dict[str, str] = {}  # node_id -> session_id (online)
        self._node_last_summary: dict[str, dict[str, object]] = {}
        self.heartbeat_interval_ms = heartbeat_interval_ms
        self.heartbeat_timeout_ms = heartbeat_timeout_ms
        self._lock = asyncio.Lock()

    # -- Registration ------------------------------------------------------

    async def register_session_locked(
        self,
        session: NodeSession,
        *,
        node_id: str,
        display_name: str,
        node_type: str,
        capabilities: list[NodeCapability],
        metadata: dict[str, object],
    ) -> str:
        """Register under the registry lock (use from async contexts)."""
        async with self._lock:
            return self._register(
                session,
                node_id=node_id,
                display_name=display_name,
                node_type=node_type,
                capabilities=capabilities,
                metadata=metadata,
            )

    def register_session(
        self,
        session: NodeSession,
        *,
        node_id: str,
        display_name: str,
        node_type: str,
        capabilities: list[NodeCapability],
        metadata: dict[str, object],
    ) -> str:
        """Synchronous registration (called from the dispatcher handler).

        Safe because the dispatcher runs on the same event loop that owns the
        session; callers awaiting the result should prefer
        ``register_session_locked``.
        """
        return self._register(
            session,
            node_id=node_id,
            display_name=display_name,
            node_type=node_type,
            capabilities=capabilities,
            metadata=metadata,
        )

    def _register(
        self,
        session: NodeSession,
        *,
        node_id: str,
        display_name: str,
        node_type: str,
        capabilities: list[NodeCapability],
        metadata: dict[str, object],
    ) -> str:
        # Assign a fresh, opaque session id (independent of transport ids).
        session_id = f"node_session_{secrets.token_urlsafe(12)}"
        session.session_id = session_id
        session.mark_online(
            node_id=node_id,
            display_name=display_name,
            node_type=node_type,
            capabilities=capabilities,
            metadata=metadata,
        )
        # Displace any existing online session for this node_id.
        old_session_id = self._by_node.get(node_id)
        if old_session_id and old_session_id in self._by_session:
            old = self._by_session[old_session_id]
            self._displace(old, new_session_id=session_id)

        self._by_session[session_id] = session
        self._by_node[node_id] = session_id
        return session_id

    def _displace(self, old: NodeSession, *, new_session_id: str) -> None:
        """Mark an old session offline and notify it of the duplicate."""
        old.mark_offline()
        # Best-effort notification; the old connection's read loop will exit.
        send = old.send
        if send is not None:
            event = build_event(
                "node.duplicate_connection",
                payload={
                    "node_id": old.node_id,
                    "new_session_id": new_session_id,
                },
            )
            try:
                loop = asyncio.get_event_loop()
                loop.create_task(_safe_send(send, event))  # type: ignore[arg-type]
            except RuntimeError:
                pass
        self._by_session.pop(old.session_id, None)

    # -- Lookup ------------------------------------------------------------

    def get_session(self, session_id: str) -> NodeSession | None:
        return self._by_session.get(session_id)

    def get_online_session(self, node_id: str) -> NodeSession | None:
        session_id = self._by_node.get(node_id)
        if session_id is None:
            return None
        session = self._by_session.get(session_id)
        if session is None or session.state != NodeSessionState.ONLINE:
            return None
        return session

    def find_capability_owner(self, capability: str) -> NodeSession | None:
        """Return an online session that registered the given capability."""
        for session in self._by_session.values():
            if session.state != NodeSessionState.ONLINE:
                continue
            if session.get_capability(capability) is not None:
                return session
        return None

    def list_online_nodes(self) -> list[dict[str, object]]:
        return [
            session.to_summary()
            for session in self._by_session.values()
            if session.state == NodeSessionState.ONLINE
        ]

    # -- Offline / cleanup -------------------------------------------------

    def mark_offline(self, session: NodeSession) -> None:
        session.mark_offline()
        # Only clear the node_id index if this session owns it.
        online_session_id = self._by_node.get(session.node_id)
        if online_session_id == session.session_id:
            self._by_node.pop(session.node_id, None)
        self._by_session.pop(session.session_id, None)
        logger.info("node_registry.marked_offline", node_id=session.node_id)

    def update_state_summary(self, node_id: str, summary: dict[str, object]) -> None:
        self._node_last_summary[node_id] = summary

    def get_state_summary(self, node_id: str) -> dict[str, object] | None:
        return self._node_last_summary.get(node_id)


async def _safe_send(send: object, envelope: object) -> None:
    try:
        await send(envelope)  # type: ignore[misc]
    except Exception:  # noqa: BLE001
        pass


__all__ = ["NodeRegistry"]
