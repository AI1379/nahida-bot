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
        self._active_runs: set[str] = set()
        self._runs_with_output: set[str] = set()

    async def start(self) -> None:
        bus = self._app.event_bus
        # Import lazily to avoid import cycles at module load.
        from nahida_bot.core.events import (
            AgentRunCancelled,
            AgentRunFinished,
            AgentRunStarted,
            MessageSent,
            PluginErrorOccurred,
        )

        for event_type in (
            AgentRunStarted,
            AgentRunFinished,
            AgentRunCancelled,
            MessageSent,
            PluginErrorOccurred,
        ):
            self._subscriptions.append(
                bus.subscribe(event_type, self._make_handler(event_type), priority=20)
            )
        logger.info("node_event_bridge.started")

    async def stop(self) -> None:
        for sub in self._subscriptions:
            sub.unsubscribe()
        self._subscriptions.clear()
        self._active_runs.clear()
        self._runs_with_output.clear()
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
        reply_route = str(envelope_payload.get("reply_route", ""))
        target_node_id = (
            reply_route.removeprefix("node:") if reply_route.startswith("node:") else ""
        )
        # Replies to node-originated turns are routed only to that authenticated
        # node. Legacy core events without an explicit route retain the V1
        # broadcast behavior until session subscription policy is implemented.
        for summary in self._registry.list_online_nodes():
            session = self._registry.get_session(summary["session_id"])  # type: ignore[arg-type]
            if session is None or session.send is None:
                continue
            if target_node_id and session.node_id != target_node_id:
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
            AgentRunCancelled,
            AgentRunFinished,
            AgentRunStarted,
            MessageSent,
            PluginErrorOccurred,
        )

        payload = event.payload
        if isinstance(event, MessageSent):
            session_id = getattr(payload, "session_id", "")
            if session_id in self._active_runs:
                self._runs_with_output.add(session_id)
            outbound = getattr(payload, "outbound", None)
            text = getattr(outbound, "text", "") if outbound is not None else ""
            extra = getattr(outbound, "extra", {}) if outbound is not None else {}
            event_payload: dict[str, Any] = {
                "session_id": getattr(payload, "session_id", ""),
                "text": text,
            }
            display_plan = (
                extra.get("display_plan") if isinstance(extra, dict) else None
            )
            if isinstance(display_plan, dict):
                event_payload["display_plan"] = display_plan
            return {
                "event": "agent.message.completed",
                "payload": event_payload,
                "reply_route": getattr(payload, "reply_route", ""),
            }
        if isinstance(event, PluginErrorOccurred):
            return {
                "event": "plugin.error",
                "payload": {
                    "plugin_id": getattr(payload, "plugin_name", ""),
                    "method": getattr(payload, "method", ""),
                    "error": getattr(payload, "error", ""),
                },
            }
        if isinstance(event, AgentRunStarted):
            session_id = getattr(payload, "session_id", "")
            if session_id:
                self._active_runs.add(session_id)
            self._runs_with_output.discard(session_id)
            return {
                "event": "agent.message.started",
                "payload": {"session_id": session_id},
            }
        if isinstance(event, (AgentRunFinished, AgentRunCancelled)):
            session_id = getattr(payload, "session_id", "")
            self._active_runs.discard(session_id)
            if session_id in self._runs_with_output:
                self._runs_with_output.discard(session_id)
                return None
            return {
                "event": "agent.message.completed",
                "payload": {
                    "session_id": session_id,
                    "text": "",
                    "terminal": getattr(payload, "terminal", ""),
                    "error": getattr(payload, "error", ""),
                },
            }
        return None


__all__ = ["NodeEventBridge"]
