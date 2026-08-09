"""Bridge core EventBus events to Gateway-Node sessions.

Session events are delivered only to the node reply route that originated the
turn, or to an online node explicitly bound to the event's conversation. Global
protocol notifications contain no session data and may be sent to every online
node.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

import structlog

from nahida_bot.gateway.node_protocol.schemas import build_event
from nahida_bot.gateway.services.node_registry import NodeRegistry

if TYPE_CHECKING:
    from nahida_bot.core.app import Application
    from nahida_bot.core.events import Event
    from nahida_bot.gateway.node_protocol.sessions import NodeSession

logger = structlog.get_logger(__name__)


class NodeEventBridge:
    """Translate core events and send them only to eligible node sessions."""

    def __init__(self, app: Application, registry: NodeRegistry) -> None:
        self._app = app
        self._registry = registry
        self._subscriptions: list[Any] = []
        self._pending_routes: dict[str, deque[str]] = defaultdict(deque)
        self._active_routes: dict[str, str] = {}

    async def start(self) -> None:
        from nahida_bot.core.events import (
            AgentRunCancelled,
            AgentRunFinished,
            AgentRunStarted,
            AppStopping,
            MessageReceived,
            MessageSent,
            PluginErrorOccurred,
            SchedulerNotification,
        )

        bus = self._app.event_bus
        # Capture the explicit node route before MessageRouter starts a
        # background run and publishes route-less AgentRun lifecycle events.
        self._subscriptions.append(
            bus.subscribe(MessageReceived, self._capture_reply_route, priority=-10)
        )
        for event_type in (
            AgentRunStarted,
            AgentRunCancelled,
            AgentRunFinished,
            MessageSent,
            PluginErrorOccurred,
            SchedulerNotification,
            AppStopping,
        ):
            self._subscriptions.append(
                bus.subscribe(event_type, self._make_handler(event_type), priority=20)
            )
        logger.info("node_event_bridge.started")

    async def stop(self) -> None:
        for sub in self._subscriptions:
            sub.unsubscribe()
        self._subscriptions.clear()
        self._pending_routes.clear()
        self._active_routes.clear()
        logger.info("node_event_bridge.stopped")

    async def _capture_reply_route(self, event: Event[Any], ctx: Any) -> None:
        payload = event.payload
        target_node_id = self._node_id_from_reply_route(
            getattr(payload, "reply_route", "")
        )
        session_id = str(getattr(payload, "session_id", ""))
        if target_node_id and session_id:
            self._pending_routes[session_id].append(target_node_id)

    def _make_handler(self, event_type: type):
        async def handler(event: Event[Any], ctx: Any) -> None:
            await self._dispatch(event_type, event)

        return handler

    async def _dispatch(self, event_type: type, event: Event[Any]) -> None:
        translated = self._translate(event_type, event)
        if translated is None:
            from nahida_bot.core.events import AgentRunCancelled, AgentRunFinished

            if isinstance(event, (AgentRunCancelled, AgentRunFinished)):
                self._active_routes.pop(str(event.payload.session_id), None)
            return
        event_name, event_payload, global_event = translated
        sessions = (
            self._online_sessions()
            if global_event
            else self._sessions_for_event(event, event_payload)
        )
        node_event = build_event(event_name, payload=event_payload)
        for session in sessions:
            if session.send is None:
                continue
            try:
                await session.send(node_event)
            except Exception:  # noqa: BLE001 - one dead node must not block others
                logger.debug(
                    "node_event_bridge.send_failed",
                    node_id=session.node_id,
                    event_name=event_name,
                )

    def _sessions_for_event(
        self, event: Event[Any], event_payload: dict[str, Any]
    ) -> list[NodeSession]:
        from nahida_bot.core.events import (
            AgentRunCancelled,
            AgentRunFinished,
            AgentRunStarted,
            MessageSent,
            SchedulerNotification,
        )

        payload = event.payload
        session_id = str(event_payload.get("session_id", ""))
        target_node_id = ""
        if isinstance(event, MessageSent):
            target_node_id = self._node_id_from_reply_route(
                getattr(payload, "reply_route", "")
            )
            if target_node_id and session_id not in self._active_routes:
                self._discard_pending_route(session_id, target_node_id)
        elif isinstance(event, AgentRunStarted):
            pending = self._pending_routes.get(session_id)
            if pending:
                target_node_id = pending.popleft()
                if not pending:
                    self._pending_routes.pop(session_id, None)
                self._active_routes[session_id] = target_node_id
            else:
                target_node_id = self._bound_node_id(session_id)
                if target_node_id:
                    self._active_routes[session_id] = target_node_id
        elif isinstance(event, (AgentRunCancelled, AgentRunFinished)):
            target_node_id = self._active_routes.pop(session_id, "")
            if not target_node_id:
                target_node_id = self._bound_node_id(session_id)
        elif isinstance(event, SchedulerNotification):
            target_node_id = self._bound_node_id(payload.conversation_id)

        session = self._registry.get_online_session(target_node_id)
        return [session] if session is not None else []

    def _translate(
        self, event_type: type, event: Event[Any]
    ) -> tuple[str, dict[str, Any], bool] | None:
        from nahida_bot.core.events import (
            AgentRunCancelled,
            AgentRunFinished,
            AgentRunStarted,
            AppStopping,
            MessageSent,
            PluginErrorOccurred,
            SchedulerNotification,
        )

        payload = event.payload
        if isinstance(event, AgentRunStarted):
            return "agent.message.started", {"session_id": payload.session_id}, False
        if isinstance(event, (AgentRunCancelled, AgentRunFinished)):
            if payload.terminal not in {"failed", "crashed", "incomplete"}:
                return None
            return (
                "agent.message.error",
                {
                    "session_id": payload.session_id,
                    "error": payload.error or f"Agent run {payload.terminal}.",
                    "terminal": payload.terminal,
                },
                False,
            )
        if isinstance(event, MessageSent):
            outbound = getattr(payload, "outbound", None)
            text = getattr(outbound, "text", "") if outbound is not None else ""
            extra = getattr(outbound, "extra", {}) if outbound is not None else {}
            event_payload: dict[str, Any] = {
                "session_id": payload.session_id,
                "text": text,
            }
            display_plan = (
                extra.get("display_plan") if isinstance(extra, dict) else None
            )
            if isinstance(display_plan, dict):
                event_payload["display_plan"] = display_plan
            return "agent.message.completed", event_payload, False
        if isinstance(event, PluginErrorOccurred):
            return (
                "plugin.error",
                {
                    "plugin_id": payload.plugin_id,
                    "plugin_name": payload.plugin_name,
                    "method": payload.method,
                    "error": payload.error,
                },
                True,
            )
        if isinstance(event, SchedulerNotification):
            event_name = (
                "notification.reminder"
                if payload.level == "reminder"
                else "notification.error"
            )
            return (
                event_name,
                {
                    "job_id": payload.job_id,
                    "session_id": payload.session_id,
                    "conversation_id": payload.conversation_id,
                    "message": payload.text,
                },
                False,
            )
        if isinstance(event, AppStopping):
            return (
                "gateway.shutdown",
                {"reason": "application_stopping", "retry_after_ms": 5000},
                True,
            )
        return None

    def _online_sessions(self) -> Iterable[NodeSession]:
        for summary in self._registry.list_online_nodes():
            session_id = summary.get("session_id")
            if not isinstance(session_id, str):
                continue
            session = self._registry.get_session(session_id)
            if session is not None:
                yield session

    def _bound_node_id(self, conversation_id: str) -> str:
        matches = {
            session.node_id
            for session in self._online_sessions()
            if session.conversation_id == conversation_id
        }
        # Ambiguous bindings are not safe to route implicitly.
        return next(iter(matches)) if len(matches) == 1 else ""

    def _discard_pending_route(self, session_id: str, node_id: str) -> None:
        pending = self._pending_routes.get(session_id)
        if not pending:
            return
        try:
            pending.remove(node_id)
        except ValueError:
            return
        if not pending:
            self._pending_routes.pop(session_id, None)

    @staticmethod
    def _node_id_from_reply_route(reply_route: object) -> str:
        route = str(reply_route)
        return route.removeprefix("node:") if route.startswith("node:") else ""


__all__ = ["NodeEventBridge"]
