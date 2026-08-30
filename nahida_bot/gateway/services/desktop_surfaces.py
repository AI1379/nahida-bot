"""Synchronize plugin-owned Desktop surface snapshots to online nodes."""

from __future__ import annotations

import asyncio
from itertools import count

import structlog

from nahida_bot.gateway.services.node_invoker import NodeInvoker
from nahida_bot.gateway.services.node_registry import NodeRegistry
from nahida_bot.plugins.desktop_surfaces import DesktopSurfaceRegistry
from nahida_bot_sdk.desktop import DesktopSurfaceContext

logger = structlog.get_logger(__name__)

DESKTOP_SURFACE_SYNC_CAPABILITY = "desktop.surface.sync"


class DesktopSurfaceService:
    """Build desired-state snapshots and deliver them through Node capabilities."""

    def __init__(
        self,
        surfaces: DesktopSurfaceRegistry,
        nodes: NodeRegistry,
        invoker: NodeInvoker,
    ) -> None:
        self._surfaces = surfaces
        self._nodes = nodes
        self._invoker = invoker
        self._revisions = count(1)
        self._sync_lock = asyncio.Lock()

    def start(self) -> None:
        self._surfaces.set_change_callback(self.sync_all)

    def stop(self) -> None:
        self._surfaces.set_change_callback(None)

    async def sync_all(self) -> None:
        node_ids = [
            str(summary["node_id"])
            for summary in self._nodes.list_online_nodes()
            if summary.get("node_type") == "desktop" and summary.get("node_id")
        ]
        if node_ids:
            await asyncio.gather(*(self.sync_node(node_id) for node_id in node_ids))

    async def sync_node(self, node_id: str) -> bool:
        async with self._sync_lock:
            return await self._sync_node(node_id)

    async def _sync_node(self, node_id: str) -> bool:
        session = self._nodes.get_online_session(node_id)
        if (
            session is None
            or session.node_type != "desktop"
            or session.get_capability(DESKTOP_SURFACE_SYNC_CAPABILITY) is None
        ):
            return False

        context = DesktopSurfaceContext(
            node_id=session.node_id,
            display_name=session.display_name,
            node_type=session.node_type,
            metadata=dict(session.metadata),
        )
        snapshot = await self._surfaces.collect(context)
        revision = next(self._revisions)
        result = await self._invoker.invoke(
            capability=DESKTOP_SURFACE_SYNC_CAPABILITY,
            arguments={
                "revision": revision,
                "surfaces": [
                    item.model_dump(mode="json", by_alias=True) for item in snapshot
                ],
            },
            caller="plugin-surfaces",
            node_id=node_id,
        )
        if not result.ok:
            logger.warning(
                "desktop_surface.sync_failed",
                node_id=node_id,
                revision=revision,
                error_code=(
                    result.error.code if result.error is not None else "failed"
                ),
            )
            return False
        logger.debug(
            "desktop_surface.synced",
            node_id=node_id,
            revision=revision,
            surfaces=len(snapshot),
        )
        return True


__all__ = ["DESKTOP_SURFACE_SYNC_CAPABILITY", "DesktopSurfaceService"]
