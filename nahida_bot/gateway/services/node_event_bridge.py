"""Bridge core EventBus events to online Gateway-Node sessions.

Node events are routed only when explicitly targeted by ``reply_route``.
A core event (e.g. ``MessageSent``) that was produced by a channel-specific
session is not replayed to every connected node — it is forwarded only to the
node identified by the ``node:`` prefix in ``reply_route``.

This is intentional: a desktop node is conceptually a channel client, not a
universal event subscriber. It should receive events for its own sessions,
just as the Milky channel does not receive Telegram session events.
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
    """Forwards explicitly node-routed events to the target node session."""

    def __init__(self, app: Application, registry: NodeRegistry) -> None:
        self._app = app
        self._registry = registry
        self._subscriptions: list[Any] = []

    async def start(self) -> None:
        bus = self._app.event_bus
        from nahida_bot.core.events import MessageSent

        self._subscriptions.append(
            bus.subscribe(MessageSent, self._make_handler(MessageSent), priority=20)
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
        reply_route = str(envelope_payload.get("reply_route", ""))
        target_node_id = (
            reply_route.removeprefix("node:") if reply_route.startswith("node:") else ""
        )
        if not target_node_id:
            return
        for summary in self._registry.list_online_nodes():
            session = self._registry.get_session(summary["session_id"])  # type: ignore[arg-type]
            if session is None or session.send is None:
                continue
            if session.node_id != target_node_id:
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
        from nahida_bot.core.events import MessageSent

        payload = event.payload
        if isinstance(event, MessageSent):
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
        return None


__all__ = ["NodeEventBridge"]
