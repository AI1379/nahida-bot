"""Bridge core EventBus events to online Gateway-Node sessions.

The broadcaster that feeds the SSE stream is per-HTTP-client. Nodes need their
own fan-out path because:

- node subscriptions are per-node and authorization-filtered,
- node events carry extra payload (e.g. ``display_plan``) not present in the
  SSE contract,
- node delivery must not block SSE clients and vice versa.

This bridge subscribes to the same core events as the SSE broadcaster but
translates them into node-protocol envelopes and pushes them to online nodes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from nahida_bot.gateway.node_protocol.schemas import build_event
from nahida_bot.gateway.services.node_registry import NodeRegistry

if TYPE_CHECKING:
    from nahida_bot.core.app import Application
    from nahida_bot.core.events import Event

logger = structlog.get_logger(__name__)


class NodeEventBridge:
    """Forwards relevant core events to all online node sessions."""

    def __init__(self, app: Application, registry: NodeRegistry) -> None:
        self._app = app
        self._registry = registry
        self._subscriptions: list[Any] = []

    async def start(self) -> None:
        bus = self._app.event_bus
        # Import lazily to avoid import cycles at module load.
        from nahida_bot.core.events import (
            AgentRunFinished,
            AgentRunStarted,
            MessageReceived,
            MessageSent,
            PluginErrorOccurred,
        )

        for event_type in (
            MessageReceived,
            MessageSent,
            PluginErrorOccurred,
            AgentRunStarted,
            AgentRunFinished,
        ):
            self._subscriptions.append(
                bus.subscribe(event_type, self._make_handler(event_type), priority=20)
            )
        logger.info("node_event_bridge.started")

    async def stop(self) -> None:
        for sub in self._subscriptions:
            sub.unsubscribe()
        self._subscriptions.clear()
        logger.info("node_event_bridge.stopped")

    def _make_handler(self, event_type: type):
        async def handler(event: Event[Any], ctx: Any) -> None:
            await self._dispatch(event_type, event)

        return handler

    async def _dispatch(self, event_type: type, event: Event[Any]) -> None:
        envelope_payload = self._translate(event_type, event)
        if envelope_payload is None:
            return
        event_name = envelope_payload["event"]
        # Fan out to every online node. V1 is permissive: all online nodes
        # receive all events. Authorization filtering is a follow-up.
        for summary in self._registry.list_online_nodes():
            session = self._registry.get_session(summary["session_id"])  # type: ignore[arg-type]
            if session is None or session.send is None:
                continue
            node_event = build_event(event_name, payload=envelope_payload["payload"])
            try:
                await session.send(node_event)
            except Exception:  # noqa: BLE001 - one dead node must not block others
                logger.debug(
                    "node_event_bridge.send_failed",
                    node_id=session.node_id,
                    event_name=event_name,
                )

    def _translate(self, event_type: type, event: Event[Any]) -> dict[str, Any] | None:
        """Map a core event to a ``(event_name, payload)`` dict, or None."""
        from nahida_bot.core.events import (
            AgentRunFinished,
            AgentRunStarted,
            MessageReceived,
            MessageSent,
            PluginErrorOccurred,
        )

        payload = event.payload
        if isinstance(event, (MessageReceived, MessageSent)):
            return {
                "event": (
                    "agent.message.started"
                    if isinstance(event, MessageReceived)
                    else "agent.message.completed"
                ),
                "payload": {"session_id": getattr(payload, "session_id", "")},
            }
        if isinstance(event, PluginErrorOccurred):
            return {
                "event": "plugin.error",
                "payload": {
                    "plugin_id": getattr(payload, "plugin_id", ""),
                    "error": getattr(payload, "error", ""),
                },
            }
        if isinstance(event, AgentRunStarted):
            return {
                "event": "agent.message.started",
                "payload": {"session_id": getattr(payload, "session_id", "")},
            }
        if isinstance(event, AgentRunFinished):
            return {
                "event": "agent.message.completed",
                "payload": {
                    "session_id": getattr(payload, "session_id", ""),
                    "terminal": getattr(payload, "terminal", ""),
                    "error": getattr(payload, "error", ""),
                },
            }
        return None


__all__ = ["NodeEventBridge"]
