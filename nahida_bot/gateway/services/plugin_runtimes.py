"""Synchronize one plugin manager's runtime-facet state to connected Nodes."""

from __future__ import annotations

import asyncio
from itertools import count
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import structlog

from nahida_bot.gateway.node_protocol.schemas import build_event

if TYPE_CHECKING:
    from nahida_bot.core.events import Event, EventBus, Subscription
    from nahida_bot.gateway.node_protocol.sessions import NodeSession
    from nahida_bot.gateway.services.node_registry import NodeRegistry
    from nahida_bot.plugins.manager import PluginManager

logger = structlog.get_logger(__name__)

PLUGIN_RUNTIME_SYNC_EVENT = "plugin.runtime.sync"


class PluginRuntimeService:
    """Publish authoritative lifecycle snapshots for all runtime facets."""

    def __init__(
        self,
        plugins: PluginManager,
        nodes: NodeRegistry,
        event_bus: EventBus,
    ) -> None:
        self._plugins = plugins
        self._nodes = nodes
        self._event_bus = event_bus
        self._generation = uuid4().hex
        self._revisions = count(1)
        self._subscriptions: list[Subscription] = []

    def start(self) -> None:
        from nahida_bot.core.events import (
            PluginDisabled,
            PluginEnabled,
            PluginErrorOccurred,
            PluginLoaded,
            PluginUnloaded,
        )

        for event_type in (
            PluginLoaded,
            PluginEnabled,
            PluginDisabled,
            PluginUnloaded,
            PluginErrorOccurred,
        ):
            self._subscriptions.append(
                self._event_bus.subscribe(event_type, self._on_plugin_change)
            )

    def stop(self) -> None:
        for subscription in self._subscriptions:
            subscription.unsubscribe()
        self._subscriptions.clear()

    def snapshot(self) -> dict[str, Any]:
        plugins = []
        for record in sorted(
            self._plugins.list_plugins(), key=lambda item: item.manifest.id
        ):
            manifest = record.manifest
            plugins.append(
                {
                    "id": manifest.id,
                    "name": manifest.name,
                    "version": manifest.version,
                    "state": record.state.value,
                    "configured_enabled": record.configured_enabled,
                    "runtimes": manifest.runtimes.model_dump(
                        mode="json", exclude_none=True
                    ),
                    "contributes": manifest.contributes.model_dump(
                        mode="json", exclude_none=True
                    ),
                }
            )
        return {
            "generation": self._generation,
            "revision": next(self._revisions),
            "plugins": plugins,
        }

    async def sync_all(self) -> None:
        sessions = self._online_sessions()
        if not sessions:
            return
        payload = self.snapshot()
        await asyncio.gather(
            *(self._send_snapshot(session, payload) for session in sessions)
        )

    async def sync_node(self, node_id: str) -> bool:
        session = self._nodes.get_online_session(node_id)
        if session is None:
            return False
        return await self._send_snapshot(session, self.snapshot())

    async def _on_plugin_change(self, event: Event[Any], ctx: Any) -> None:
        await self.sync_all()

    def _online_sessions(self) -> list[NodeSession]:
        sessions = []
        for summary in self._nodes.list_online_nodes():
            session_id = summary.get("session_id")
            if not isinstance(session_id, str):
                continue
            session = self._nodes.get_session(session_id)
            if session is not None:
                sessions.append(session)
        return sessions

    async def _send_snapshot(
        self, session: NodeSession, payload: dict[str, Any]
    ) -> bool:
        if session.send is None:
            return False
        sent = await session.send(
            build_event(PLUGIN_RUNTIME_SYNC_EVENT, payload=payload)
        )
        if not sent:
            logger.debug(
                "plugin_runtime.sync_failed",
                node_id=session.node_id,
                revision=payload["revision"],
            )
            return False
        logger.debug(
            "plugin_runtime.synced",
            node_id=session.node_id,
            revision=payload["revision"],
            plugins=len(payload["plugins"]),
        )
        return True


__all__ = ["PLUGIN_RUNTIME_SYNC_EVENT", "PluginRuntimeService"]
