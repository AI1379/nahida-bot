"""Registry for plugin-owned, host-rendered Desktop surfaces."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog

from nahida_bot_sdk.desktop import (
    DesktopSurfaceContext,
    DesktopSurfaceKind,
    DesktopSurfaceSnapshotItem,
    DesktopSurfaceTarget,
    DesktopSurfaceView,
)

logger = structlog.get_logger(__name__)

DesktopSurfaceProvider = Callable[
    [DesktopSurfaceContext], Awaitable[DesktopSurfaceView | None]
]
DesktopSurfaceChangeCallback = Callable[[], Awaitable[None] | None]


@dataclass(slots=True, frozen=True)
class DesktopSurfaceProviderEntry:
    """One active provider, with ownership fixed by the plugin host."""

    plugin_id: str
    surface_id: str
    target: DesktopSurfaceTarget
    kind: DesktopSurfaceKind
    priority: int
    handler: DesktopSurfaceProvider

    @property
    def key(self) -> str:
        return f"{self.plugin_id}:{self.surface_id}"


class DesktopSurfaceRegistry:
    """Tracks active providers and resolves a snapshot for one Desktop node."""

    def __init__(self, *, provider_timeout_seconds: float = 5.0) -> None:
        self._entries: dict[str, DesktopSurfaceProviderEntry] = {}
        self._provider_timeout_seconds = provider_timeout_seconds
        self._change_callback: DesktopSurfaceChangeCallback | None = None

    def set_change_callback(
        self, callback: DesktopSurfaceChangeCallback | None
    ) -> None:
        self._change_callback = callback

    def register(self, entry: DesktopSurfaceProviderEntry) -> None:
        if entry.key in self._entries:
            raise KeyError(
                f"Desktop surface provider '{entry.key}' is already registered"
            )
        self._entries[entry.key] = entry
        self._notify_changed()

    def unregister(self, plugin_id: str, surface_id: str) -> bool:
        removed = self._entries.pop(f"{plugin_id}:{surface_id}", None) is not None
        if removed:
            self._notify_changed()
        return removed

    def unregister_by_plugin(self, plugin_id: str) -> int:
        keys = [
            key for key, entry in self._entries.items() if entry.plugin_id == plugin_id
        ]
        for key in keys:
            self._entries.pop(key, None)
        if keys:
            self._notify_changed()
        return len(keys)

    def refresh(self, plugin_id: str, surface_id: str) -> bool:
        if f"{plugin_id}:{surface_id}" not in self._entries:
            return False
        self._notify_changed()
        return True

    def list_entries(self) -> list[DesktopSurfaceProviderEntry]:
        return list(self._entries.values())

    async def collect(
        self, context: DesktopSurfaceContext
    ) -> list[DesktopSurfaceSnapshotItem]:
        entries = tuple(self._entries.values())
        resolved = await asyncio.gather(
            *(self._resolve_entry(entry, context) for entry in entries)
        )
        surfaces = [surface for surface in resolved if surface is not None]
        return sorted(
            surfaces, key=lambda item: (-item.priority, item.owner_plugin_id, item.id)
        )

    async def _resolve_entry(
        self,
        entry: DesktopSurfaceProviderEntry,
        context: DesktopSurfaceContext,
    ) -> DesktopSurfaceSnapshotItem | None:
        try:
            view = await asyncio.wait_for(
                entry.handler(context), timeout=self._provider_timeout_seconds
            )
        except Exception:  # noqa: BLE001 - one plugin must not block the snapshot
            logger.exception(
                "desktop_surface.provider_failed",
                plugin_id=entry.plugin_id,
                surface_id=entry.surface_id,
                node_id=context.node_id,
            )
            return None
        if view is None:
            return None
        if not isinstance(view, DesktopSurfaceView):
            try:
                view = DesktopSurfaceView.model_validate(view)
            except Exception:  # noqa: BLE001 - invalid plugin return value
                logger.warning(
                    "desktop_surface.provider_invalid",
                    plugin_id=entry.plugin_id,
                    surface_id=entry.surface_id,
                    node_id=context.node_id,
                )
                return None
        try:
            return DesktopSurfaceSnapshotItem(
                owner_plugin_id=entry.plugin_id,
                id=entry.surface_id,
                target=entry.target,
                kind=entry.kind,
                priority=entry.priority,
                view=view,
            )
        except Exception:  # noqa: BLE001 - invalid ownership metadata
            logger.warning(
                "desktop_surface.provider_invalid_identity",
                plugin_id=entry.plugin_id,
                surface_id=entry.surface_id,
                node_id=context.node_id,
            )
            return None

    def _notify_changed(self) -> None:
        callback = self._change_callback
        if callback is None:
            return
        try:
            result = callback()
        except Exception:  # noqa: BLE001 - sync must not break plugin lifecycle
            logger.exception("desktop_surface.change_callback_failed")
            return
        if not inspect.isawaitable(result):
            return
        guarded = self._await_change_callback(result)
        try:
            asyncio.ensure_future(guarded)
        except RuntimeError:
            # Registration can happen in a synchronous unit test without a loop.
            guarded.close()
            if inspect.iscoroutine(result):
                result.close()

    async def _await_change_callback(self, result: Awaitable[None]) -> None:
        try:
            await result
        except Exception:  # noqa: BLE001 - sync must not break plugin lifecycle
            logger.exception("desktop_surface.change_callback_failed")


__all__ = [
    "DesktopSurfaceProvider",
    "DesktopSurfaceProviderEntry",
    "DesktopSurfaceRegistry",
]
