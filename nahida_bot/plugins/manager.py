"""Plugin lifecycle manager — orchestrates discovery, loading, and isolation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from nahida_bot.core.exceptions import PluginLoadError, PluginStateError
from nahida_bot.plugins.api_bridge import RealBotAPI
from nahida_bot.plugins.permissions import PermissionChecker
from nahida_bot.plugins.loader import PluginLoader
from nahida_bot.plugins.manifest import PluginManifest, parse_manifest
from nahida_bot.plugins.registry import HandlerRegistry, ToolRegistry

if TYPE_CHECKING:
    from nahida_bot.agent.memory.store import MemoryStore
    from nahida_bot.core.events import EventBus
    from nahida_bot.plugins.base import Plugin
    from nahida_bot.workspace.manager import WorkspaceManager

logger = structlog.get_logger(__name__)


class PluginState(StrEnum):
    """Lifecycle states for a plugin."""

    FOUND = "found"  # Manifest discovered on disk
    LOADED = "loaded"  # Module imported, class instantiated
    ENABLED = "enabled"  # on_load + on_enable called, handlers active
    DISABLED = "disabled"  # on_disable called, handlers removed
    ERROR = "error"  # Plugin crashed; no further dispatch
    UNLOADED = "unloaded"  # Fully cleaned up


@dataclass(slots=True)
class PluginRecord:
    """Internal bookkeeping for one plugin."""

    manifest: PluginManifest
    plugin_dir: Path
    state: PluginState = PluginState.FOUND
    instance: Plugin | None = None
    api_bridge: RealBotAPI | None = None
    error_message: str = ""


class PluginManager:
    """Manages the full lifecycle of all plugins.

    Usage::

        manager = PluginManager(event_bus=event_bus, ...)
        await manager.discover([Path("plugins")])
        await manager.load_all()
        await manager.enable_all()
        # ... bot runs ...
        await manager.shutdown_all()
    """

    def __init__(
        self,
        event_bus: EventBus,
        workspace_manager: WorkspaceManager | None = None,
        memory_store: MemoryStore | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._workspace = workspace_manager
        self._memory = memory_store
        self._loader = PluginLoader()
        self._tool_registry = ToolRegistry()
        self._handler_registry = HandlerRegistry()
        self._records: dict[str, PluginRecord] = {}

    @property
    def tool_registry(self) -> ToolRegistry:
        """Public read-only access to the tool registry."""
        return self._tool_registry

    @property
    def handler_registry(self) -> HandlerRegistry:
        """Public read-only access to the handler registry."""
        return self._handler_registry

    def get_record(self, plugin_id: str) -> PluginRecord | None:
        """Look up a plugin record by ID."""
        return self._records.get(plugin_id)

    def list_plugins(self) -> list[PluginRecord]:
        """Return all known plugin records."""
        return list(self._records.values())

    # ── Discovery ──────────────────────────────────────

    async def discover(self, paths: list[Path]) -> list[PluginManifest]:
        """Scan directories for plugins and register discovered manifests.

        Returns:
            List of newly discovered manifests.
        """
        discovered: list[PluginManifest] = []
        for manifest, plugin_dir in self._loader.discover(paths):
            if manifest.id in self._records:
                logger.debug(
                    "plugin_manager.already_known",
                    plugin_id=manifest.id,
                )
                continue
            self._records[manifest.id] = PluginRecord(
                manifest=manifest,
                plugin_dir=plugin_dir,
            )
            discovered.append(manifest)
            logger.info(
                "plugin_manager.discovered",
                plugin_id=manifest.id,
                plugin_name=manifest.name,
                version=manifest.version,
            )
        return discovered

    # ── Loading ────────────────────────────────────────

    async def load(self, plugin_id: str) -> None:
        """Load a discovered plugin: import module, instantiate class."""
        record = self._require_record(plugin_id)
        self._require_state(record, PluginState.FOUND)

        try:
            plugin_class = self._loader.load(record.manifest, record.plugin_dir)
        except PluginLoadError:
            record.state = PluginState.ERROR
            raise

        checker = PermissionChecker(record.manifest)
        api_bridge = RealBotAPI(
            plugin_id=plugin_id,
            manifest=record.manifest,
            event_bus=self._event_bus,
            workspace_manager=self._workspace,
            memory_store=self._memory,
            permission_checker=checker,
            tool_registry=self._tool_registry,
            handler_registry=self._handler_registry,
        )

        instance = plugin_class(api=api_bridge, manifest=record.manifest)
        record.instance = instance
        record.api_bridge = api_bridge
        record.state = PluginState.LOADED

        logger.info(
            "plugin_manager.loaded",
            plugin_id=plugin_id,
        )
        await self._publish_plugin_event("PluginLoaded", record)

    async def load_all(self) -> None:
        """Load all discovered plugins. Errors are logged, not raised."""
        for plugin_id in list(self._records):
            if self._records[plugin_id].state == PluginState.FOUND:
                await self._safe_call(plugin_id, "load")

    # ── Enabling ───────────────────────────────────────

    async def enable(self, plugin_id: str) -> None:
        """Enable a loaded plugin: call on_enable (and on_load for first enable)."""
        record = self._require_record(plugin_id)
        prev_state = record.state
        self._require_state(record, PluginState.LOADED, PluginState.DISABLED)
        assert record.instance is not None

        # Only call on_load on first enable (LOADED → ENABLED).
        # Re-enabling from DISABLED skips on_load to avoid duplicate init.
        if prev_state == PluginState.LOADED:
            await self._safe_invoke(record.instance, "on_load")
        await self._safe_invoke(record.instance, "on_enable")

        if record.state != PluginState.ERROR:
            record.state = PluginState.ENABLED
            await self._publish_plugin_event("PluginEnabled", record)

    async def enable_all(self) -> None:
        """Enable all loaded plugins."""
        for plugin_id in list(self._records):
            record = self._records[plugin_id]
            if record.state in (PluginState.LOADED, PluginState.DISABLED):
                await self._safe_call(plugin_id, "enable")

    # ── Disabling ──────────────────────────────────────

    async def disable(self, plugin_id: str) -> None:
        """Disable an enabled plugin: remove handlers, call on_disable."""
        record = self._require_record(plugin_id)
        self._require_state(record, PluginState.ENABLED)

        # Remove registered tools and handlers
        self._tool_registry.unregister_by_plugin(plugin_id)
        self._handler_registry.unregister_by_plugin(plugin_id)

        # Unsubscribe from EventBus
        if record.api_bridge is not None:
            record.api_bridge.clear_subscriptions()

        if record.instance is not None:
            await self._safe_invoke(record.instance, "on_disable")

        if record.state != PluginState.ERROR:
            record.state = PluginState.DISABLED
            await self._publish_plugin_event("PluginDisabled", record)

    # ── Reloading ──────────────────────────────────────

    async def reload(self, plugin_id: str) -> None:
        """Hot-reload: disable → unload → load → enable."""
        record = self._require_record(plugin_id)

        if record.state == PluginState.ENABLED:
            await self.disable(plugin_id)
        if record.state in (PluginState.DISABLED, PluginState.LOADED):
            await self.unload(plugin_id)

        # Re-read manifest from disk
        manifest_path = record.plugin_dir / "plugin.yaml"
        new_manifest = parse_manifest(manifest_path)
        record.manifest = new_manifest
        record.state = PluginState.FOUND

        await self.load(plugin_id)
        await self.enable(plugin_id)

    # ── Unloading ──────────────────────────────────────

    async def unload(self, plugin_id: str) -> None:
        """Unload a plugin: call on_unload, release resources."""
        record = self._require_record(plugin_id)
        self._require_state(
            record, PluginState.DISABLED, PluginState.LOADED, PluginState.ERROR
        )

        if record.instance is not None:
            await self._safe_invoke(record.instance, "on_unload")

        self._loader.unload(record.manifest)
        record.instance = None
        record.api_bridge = None
        record.state = PluginState.UNLOADED

        await self._publish_plugin_event("PluginUnloaded", record)

    # ── Shutdown ───────────────────────────────────────

    async def shutdown_all(self) -> None:
        """Disable and unload all active plugins in reverse insertion order."""
        # Reverse order so that plugins loaded later (which may depend on
        # earlier ones) are shut down first.
        reversed_ids = list(reversed(self._records))

        # Disable all enabled plugins
        for plugin_id in reversed_ids:
            record = self._records[plugin_id]
            if record.state == PluginState.ENABLED:
                await self._safe_call(plugin_id, "disable")

        # Unload everything that's loaded or in error state
        for plugin_id in reversed_ids:
            record = self._records[plugin_id]
            if record.state in (
                PluginState.DISABLED,
                PluginState.LOADED,
                PluginState.ERROR,
            ):
                await self._safe_call(plugin_id, "unload")

    # ── Internal Helpers ───────────────────────────────

    def _require_record(self, plugin_id: str) -> PluginRecord:
        record = self._records.get(plugin_id)
        if record is None:
            raise PluginStateError(f"Plugin '{plugin_id}' is not discovered")
        return record

    def _require_state(self, record: PluginRecord, *allowed: PluginState) -> None:
        if record.state not in allowed:
            allowed_str = " or ".join(a.value for a in allowed)
            raise PluginStateError(
                f"Plugin '{record.manifest.id}' is in state '{record.state.value}', "
                f"expected {allowed_str}"
            )

    async def _safe_call(self, plugin_id: str, method: str) -> None:
        """Call a manager method with exception isolation."""
        try:
            fn = getattr(self, method)
            await fn(plugin_id)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "plugin_manager.operation_failed",
                plugin_id=plugin_id,
                method=method,
                error=str(exc),
            )

    async def _safe_invoke(
        self, plugin: Plugin, method_name: str, *, timeout: float = 60.0
    ) -> None:
        """Safely call a plugin lifecycle method with timeout and isolation."""
        method = getattr(plugin, method_name, None)
        if method is None:
            return

        record = self._records.get(plugin.manifest.id)
        try:
            await asyncio.wait_for(method(), timeout=timeout)
        except TimeoutError:
            msg = f"Plugin method '{method_name}' timed out after {timeout}s"
            logger.error(
                "plugin_manager.method_timeout",
                plugin_id=plugin.manifest.id,
                method=method_name,
            )
            if record is not None:
                record.state = PluginState.ERROR
                record.error_message = msg
            await self._publish_error_event(plugin, method_name, msg)
        except Exception as exc:  # noqa: BLE001
            msg = f"{type(exc).__name__}: {exc}"
            logger.exception(
                "plugin_manager.method_error",
                plugin_id=plugin.manifest.id,
                method=method_name,
            )
            if record is not None:
                record.state = PluginState.ERROR
                record.error_message = msg
            await self._publish_error_event(plugin, method_name, msg)

    async def _publish_plugin_event(
        self, event_name: str, record: PluginRecord
    ) -> None:
        """Publish a plugin lifecycle event."""
        from nahida_bot.core.events import (
            PluginDisabled,
            PluginEnabled,
            PluginLoaded,
            PluginPayload,
            PluginUnloaded,
        )

        payload = PluginPayload(
            plugin_id=record.manifest.id,
            plugin_name=record.manifest.name,
            plugin_version=record.manifest.version,
        )
        event_map: dict[str, type] = {
            "PluginLoaded": PluginLoaded,
            "PluginEnabled": PluginEnabled,
            "PluginDisabled": PluginDisabled,
            "PluginUnloaded": PluginUnloaded,
        }
        event_cls = event_map.get(event_name)
        if event_cls is not None:
            await self._event_bus.publish(event_cls(payload=payload))

    async def _publish_error_event(
        self, plugin: Plugin, method: str, error: str
    ) -> None:
        """Publish a PluginErrorOccurred event."""
        from nahida_bot.core.events import PluginErrorOccurred, PluginErrorPayload

        await self._event_bus.publish(
            PluginErrorOccurred(
                payload=PluginErrorPayload(
                    plugin_id=plugin.manifest.id,
                    plugin_name=plugin.manifest.name,
                    method=method,
                    error=error,
                )
            )
        )
