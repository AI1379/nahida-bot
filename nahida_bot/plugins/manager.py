"""Plugin lifecycle manager — orchestrates discovery, loading, and isolation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from nahida_bot.core.exceptions import PluginLoadError, PluginStateError
from nahida_bot.plugins.api_bridge import RealBotAPI
from nahida_bot.plugins.commands import CommandRegistry
from nahida_bot.plugins.permissions import PermissionChecker
from nahida_bot.plugins.loader import PluginLoader
from nahida_bot.plugins.desktop_surfaces import DesktopSurfaceRegistry
from nahida_bot.plugins.manifest import PluginManifest, parse_manifest
from nahida_bot.plugins.registry import (
    HandlerRegistry,
    PromptSupplementRegistry,
    StatusProviderRegistry,
    ToolRegistry,
)

if TYPE_CHECKING:
    from nahida_bot.agent.memory.sqlite import SQLiteMemoryStore
    from nahida_bot.core.events import EventBus
    from nahida_bot.core.temp_files import ManagedTempFileService
    from nahida_bot.db.repositories.sqlite_message_delivery_repo import (
        SQLiteMessageDeliveryStore,
    )
    from nahida_bot.db.repositories.sqlite_plugin_data_repo import (
        SQLitePluginDataRepository,
    )
    from nahida_bot.db.repositories.sqlite_plugin_secret_repo import (
        SQLitePluginSecretRepository,
    )
    from nahida_bot.plugins.base import Plugin
    from nahida_bot.workspace.manager import WorkspaceManager

logger = structlog.get_logger(__name__)


class PluginState(StrEnum):
    """Lifecycle states for a plugin."""

    FOUND = "found"  # Manifest discovered on disk
    LOADED = "loaded"  # Module imported and instantiated, but not active
    ENABLED = "enabled"  # on_load + on_enable called, handlers active
    DISABLED = "disabled"  # Inactive and fully unloaded by framework policy
    ERROR = "error"  # Plugin crashed; no further dispatch
    UNLOADED = "unloaded"  # Fully cleaned up


@dataclass(slots=True)
class PluginRecord:
    """Internal bookkeeping for one plugin."""

    manifest: PluginManifest
    plugin_dir: Path
    state: PluginState = PluginState.FOUND
    configured_enabled: bool = True
    config_overrides: dict[str, Any] | None = None
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
        memory_store: SQLiteMemoryStore | None = None,
        message_delivery_store: SQLiteMessageDeliveryStore | None = None,
        plugin_data_repo: SQLitePluginDataRepository | None = None,
        plugin_secret_repo: SQLitePluginSecretRepository | None = None,
        channel_registry: Any | None = None,
        provider_manager: Any | None = None,
        model_router: Any | None = None,
        scheduler_service: Any | None = None,
        orchestration_service: Any | None = None,
        webhost_service: Any | None = None,
        task_manager: Any | None = None,
        temp_file_service: ManagedTempFileService | None = None,
        memory_soft_scope: bool = False,
        memory_cross_chat_enabled: bool = True,
        memory_cross_chat_weights: dict[str, float] | None = None,
        speech_service: Any | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._workspace = workspace_manager
        self._memory = memory_store
        self._memory_soft_scope = memory_soft_scope
        self._memory_cross_chat_enabled = memory_cross_chat_enabled
        self._memory_cross_chat_weights = memory_cross_chat_weights
        self._message_delivery_store = message_delivery_store
        self._plugin_data_repo = plugin_data_repo
        self._plugin_secret_repo = plugin_secret_repo
        self._channel_registry = channel_registry
        self._provider_manager = provider_manager
        self._model_router = model_router
        self._scheduler_service = scheduler_service
        self._orchestration_service = orchestration_service
        self._webhost_service = webhost_service
        self._task_manager = task_manager
        self._temp_file_service = temp_file_service
        self._speech_service = speech_service
        self._document_store_manager: Any | None = None
        self._chat_metadata_store: Any | None = None
        self._loader = PluginLoader()
        self._tool_registry = ToolRegistry()
        self._handler_registry = HandlerRegistry()
        self._command_registry = CommandRegistry()
        self._supplement_registry = PromptSupplementRegistry()
        self._status_provider_registry = StatusProviderRegistry()
        self._desktop_surface_registry = DesktopSurfaceRegistry()
        self._records: dict[str, PluginRecord] = {}

    def set_runtime_services(
        self,
        *,
        workspace_manager: WorkspaceManager | None = None,
        memory_store: SQLiteMemoryStore | None = None,
        message_delivery_store: SQLiteMessageDeliveryStore | None = None,
        plugin_data_repo: SQLitePluginDataRepository | None = None,
        plugin_secret_repo: SQLitePluginSecretRepository | None = None,
        provider_manager: Any | None = None,
        model_router: Any | None = None,
        scheduler_service: Any | None = None,
        orchestration_service: Any | None = None,
        webhost_service: Any | None = None,
        task_manager: Any | None = None,
        document_store_manager: Any | None = None,
        chat_metadata_store: Any | None = None,
        temp_file_service: ManagedTempFileService | None = None,
        speech_service: Any | None = None,
    ) -> None:
        """Update services injected into subsequently loaded plugin API bridges."""
        self._workspace = workspace_manager
        self._memory = memory_store
        self._message_delivery_store = message_delivery_store
        if plugin_data_repo is not None:
            self._plugin_data_repo = plugin_data_repo
        if plugin_secret_repo is not None:
            self._plugin_secret_repo = plugin_secret_repo
        self._provider_manager = provider_manager
        self._model_router = model_router
        self._scheduler_service = scheduler_service
        self._orchestration_service = orchestration_service
        if webhost_service is not None:
            self._webhost_service = webhost_service
        if task_manager is not None:
            self._task_manager = task_manager
        if document_store_manager is not None:
            self._document_store_manager = document_store_manager
        if chat_metadata_store is not None:
            self._chat_metadata_store = chat_metadata_store
        if temp_file_service is not None:
            self._temp_file_service = temp_file_service
        if speech_service is not None:
            self._speech_service = speech_service
        for record in self._records.values():
            if record.api_bridge is not None:
                record.api_bridge.set_runtime_services(
                    workspace_manager=workspace_manager,
                    memory_store=memory_store,
                    message_delivery_store=message_delivery_store,
                    plugin_data_repo=self._plugin_data_repo,
                    plugin_secret_repo=self._plugin_secret_repo,
                    provider_manager=provider_manager,
                    scheduler_service=scheduler_service,
                    orchestration_service=orchestration_service,
                    webhost_service=self._webhost_service,
                    task_manager=self._task_manager,
                    document_store_manager=document_store_manager,
                    chat_metadata_store=self._chat_metadata_store,
                    temp_file_service=self._temp_file_service,
                )

    @property
    def tool_registry(self) -> ToolRegistry:
        """Public read-only access to the tool registry."""
        return self._tool_registry

    @property
    def handler_registry(self) -> HandlerRegistry:
        """Public read-only access to the handler registry."""
        return self._handler_registry

    @property
    def command_registry(self) -> CommandRegistry:
        """Public read-only access to the command registry."""
        return self._command_registry

    @property
    def supplement_registry(self) -> PromptSupplementRegistry:
        """Public read-only access to the prompt supplement registry."""
        return self._supplement_registry

    @property
    def status_provider_registry(self) -> StatusProviderRegistry:
        """Public read-only access to the status provider registry."""
        return self._status_provider_registry

    @property
    def desktop_surface_registry(self) -> DesktopSurfaceRegistry:
        """Registry of active host-rendered Desktop surface providers."""
        return self._desktop_surface_registry

    @property
    def scheduler_service(self) -> Any | None:
        return self._scheduler_service

    @scheduler_service.setter
    def scheduler_service(self, value: Any | None) -> None:
        self._scheduler_service = value

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
                state=(PluginState.FOUND if manifest.enabled else PluginState.DISABLED),
                configured_enabled=manifest.enabled,
            )
            discovered.append(manifest)
            logger.info(
                "plugin_manager.discovered",
                plugin_id=manifest.id,
                plugin_name=manifest.name,
                version=manifest.version,
            )
        return discovered

    def apply_config(self, plugin_id: str, config: dict[str, Any]) -> None:
        """Apply application config and consume framework-reserved fields.

        ``enabled`` is owned by the plugin host.  It is intentionally removed
        before the remaining business config is passed to the plugin instance.
        """
        record = self._require_record(plugin_id)
        overrides = dict(config)
        configured_enabled = overrides.pop("enabled", record.manifest.enabled)
        if not isinstance(configured_enabled, bool):
            raise ValueError(f"Plugin '{plugin_id}' enabled must be a boolean")

        merged = {**record.manifest.config, **overrides}
        record.manifest = record.manifest.model_copy(update={"config": merged})
        record.configured_enabled = configured_enabled
        record.config_overrides = dict(config)

        if record.instance is None:
            if configured_enabled and record.state == PluginState.DISABLED:
                record.state = PluginState.FOUND
            elif not configured_enabled and record.state == PluginState.FOUND:
                record.state = PluginState.DISABLED

    # ── Loading ────────────────────────────────────────

    async def load(self, plugin_id: str) -> None:
        """Load a discovered plugin: import module, instantiate class."""
        record = self._require_record(plugin_id)
        self._require_state(
            record,
            PluginState.FOUND,
            PluginState.DISABLED,
            PluginState.UNLOADED,
        )

        if record.manifest.runtimes.gateway is None:
            record.state = PluginState.LOADED
            record.error_message = ""
            logger.info(
                "plugin_manager.loaded_facets_only",
                plugin_id=plugin_id,
            )
            await self._publish_plugin_event("PluginLoaded", record)
            return

        try:
            plugin_class = self._loader.load(record.manifest, record.plugin_dir)
        except PluginLoadError as exc:
            record.state = PluginState.ERROR
            record.error_message = str(exc)
            raise

        checker = PermissionChecker(record.manifest)

        # TODO: Use a simpler way to inject dependencies into the API bridge
        # instead of passing everything through the constructor. Maybe a context
        # object or direct references to the manager's registries and services?
        api_bridge = RealBotAPI(
            plugin_id=plugin_id,
            manifest=record.manifest,
            event_bus=self._event_bus,
            workspace_manager=self._workspace,
            memory_store=self._memory,
            memory_soft_scope=self._memory_soft_scope,
            memory_cross_chat_enabled=self._memory_cross_chat_enabled,
            memory_cross_chat_weights=self._memory_cross_chat_weights,
            message_delivery_store=self._message_delivery_store,
            plugin_data_repo=self._plugin_data_repo,
            plugin_secret_repo=self._plugin_secret_repo,
            permission_checker=checker,
            tool_registry=self._tool_registry,
            handler_registry=self._handler_registry,
            command_registry=self._command_registry,
            supplement_registry=self._supplement_registry,
            status_provider_registry=self._status_provider_registry,
            desktop_surface_registry=self._desktop_surface_registry,
            webhost_service=self._webhost_service,
            channel_registry=self._channel_registry,
            provider_manager=self._provider_manager,
            model_router=self._model_router,
            scheduler_service=self._scheduler_service,
            orchestration_service=self._orchestration_service,
            task_manager=self._task_manager,
            document_store_manager=self._document_store_manager,
            temp_file_service=self._temp_file_service,
            speech_service=self._speech_service,
        )

        try:
            instance = plugin_class(api=api_bridge, manifest=record.manifest)
        except Exception as exc:  # noqa: BLE001
            self._loader.unload(record.manifest)
            record.state = PluginState.ERROR
            record.error_message = f"{type(exc).__name__}: {exc}"
            raise PluginLoadError(
                f"Plugin '{plugin_id}' failed to initialize: {exc}"
            ) from exc
        api_bridge.add_decorated_registrations(instance)
        record.instance = instance
        record.api_bridge = api_bridge
        record.state = PluginState.LOADED

        logger.info(
            "plugin_manager.loaded",
            plugin_id=plugin_id,
        )
        await self._publish_plugin_event("PluginLoaded", record)

    async def load_all(self, *, phase: str | None = None) -> None:
        """Load configured plugins. Errors are logged, not raised."""
        for plugin_id in list(self._records):
            record = self._records[plugin_id]
            if phase is not None and record.manifest.load_phase != phase:
                continue
            if record.configured_enabled and record.state in (
                PluginState.FOUND,
                PluginState.UNLOADED,
            ):
                await self._safe_call(plugin_id, "load")

    # ── Enabling ───────────────────────────────────────

    async def enable(self, plugin_id: str) -> None:
        """Ensure a plugin is loaded, then activate its lifecycle."""
        record = self._require_record(plugin_id)
        self._require_state(
            record,
            PluginState.FOUND,
            PluginState.LOADED,
            PluginState.DISABLED,
            PluginState.UNLOADED,
        )

        if record.state != PluginState.LOADED:
            await self.load(plugin_id)
            if record.state != PluginState.LOADED:
                return

        if record.manifest.runtimes.gateway is None:
            record.state = PluginState.ENABLED
            record.error_message = ""
            await self._publish_plugin_event("PluginEnabled", record)
            return

        assert record.instance is not None
        assert record.api_bridge is not None

        if not await self._safe_activate_registrations(record):
            return

        await self._safe_invoke(record.instance, "on_load")
        if record.state == PluginState.ERROR:
            self._clear_plugin_registrations(plugin_id, record)
            return
        await self._safe_invoke(record.instance, "on_enable")

        if record.state != PluginState.ERROR:
            record.state = PluginState.ENABLED
            await self._publish_plugin_event("PluginEnabled", record)
        else:
            self._clear_plugin_registrations(plugin_id, record)

    async def enable_all(self, *, phase: str | None = None) -> None:
        """Enable all plugins whose framework configuration enables them."""
        for plugin_id in list(self._records):
            record = self._records[plugin_id]
            if phase is not None and record.manifest.load_phase != phase:
                continue
            if record.configured_enabled and record.state in (
                PluginState.FOUND,
                PluginState.LOADED,
                PluginState.DISABLED,
                PluginState.UNLOADED,
            ):
                await self._safe_call(plugin_id, "enable")

    # ── Disabling ──────────────────────────────────────

    async def disable(self, plugin_id: str) -> None:
        """Deactivate an enabled plugin and release its loaded instance."""
        record = self._require_record(plugin_id)
        self._require_state(record, PluginState.ENABLED)

        if record.api_bridge is not None:
            record.api_bridge.deactivate_registrations()

        # Cancel all background tasks owned by this plugin
        if self._task_manager is not None:
            await self._task_manager.cancel_by_owner_and_await(plugin_id, timeout=5.0)

        if record.instance is not None:
            await self._safe_invoke(record.instance, "on_disable")

        if record.state == PluginState.ERROR:
            self._clear_plugin_registrations(plugin_id, record)
            return

        if record.instance is not None:
            await self._safe_invoke(record.instance, "on_unload")
        if record.state == PluginState.ERROR:
            self._clear_plugin_registrations(plugin_id, record)
            return

        if record.api_bridge is not None:
            record.api_bridge.clear_registrations()
        if record.manifest.runtimes.gateway is not None:
            self._loader.unload(record.manifest)
        record.instance = None
        record.api_bridge = None
        record.state = PluginState.DISABLED
        await self._publish_plugin_event("PluginDisabled", record)

    # ── Reloading ──────────────────────────────────────

    async def reload(self, plugin_id: str) -> None:
        """Re-read a plugin and restore its previous runtime activation level."""
        record = self._require_record(plugin_id)
        should_reenable = record.state in (PluginState.ENABLED, PluginState.ERROR)
        was_loaded = record.state == PluginState.LOADED

        if record.state == PluginState.ENABLED:
            await self.disable(plugin_id)
        elif record.state in (PluginState.LOADED, PluginState.ERROR):
            await self.unload(plugin_id)

        # Re-read manifest from disk
        manifest_path = record.plugin_dir / "plugin.yaml"
        new_manifest = parse_manifest(manifest_path)
        record.manifest = new_manifest
        record.configured_enabled = new_manifest.enabled
        record.state = (
            PluginState.FOUND if new_manifest.enabled else PluginState.DISABLED
        )
        if record.config_overrides is not None:
            self.apply_config(plugin_id, record.config_overrides)

        if should_reenable:
            await self.enable(plugin_id)
        elif was_loaded:
            await self.load(plugin_id)

    # ── Unloading ──────────────────────────────────────

    async def unload(self, plugin_id: str) -> None:
        """Unload a plugin: call on_unload, release resources."""
        record = self._require_record(plugin_id)
        self._require_state(record, PluginState.LOADED, PluginState.ERROR)

        if record.instance is not None:
            await self._safe_invoke(record.instance, "on_unload")
        if record.api_bridge is not None:
            record.api_bridge.clear_registrations()

        # Cancel any remaining background tasks owned by this plugin
        if self._task_manager is not None:
            await self._task_manager.cancel_by_owner_and_await(plugin_id, timeout=5.0)

        if record.manifest.runtimes.gateway is not None:
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

        # Disable all enabled plugins. Disable also unloads their instances.
        for plugin_id in reversed_ids:
            record = self._records[plugin_id]
            if record.state == PluginState.ENABLED:
                await self._safe_call(plugin_id, "disable")

        # Unload everything still loaded or in error state.
        for plugin_id in reversed_ids:
            record = self._records[plugin_id]
            if record.state in (
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

    def _clear_plugin_registrations(self, plugin_id: str, record: PluginRecord) -> None:
        """Remove runtime registrations left behind by a failed plugin hook."""
        if record.api_bridge is not None:
            record.api_bridge.clear_registrations()
        else:
            self._tool_registry.unregister_by_plugin(plugin_id)
            self._handler_registry.unregister_by_plugin(plugin_id)
            self._command_registry.unregister_by_plugin(plugin_id)
            self._supplement_registry.unregister_by_plugin(plugin_id)
            self._status_provider_registry.unregister_by_plugin(plugin_id)

    async def _safe_activate_registrations(self, record: PluginRecord) -> bool:
        """Activate remembered plugin registrations with error isolation."""
        if record.api_bridge is None or record.instance is None:
            return False
        try:
            record.api_bridge.activate_registrations()
            return True
        except Exception as exc:  # noqa: BLE001
            msg = f"{type(exc).__name__}: {exc}"
            logger.exception(
                "plugin_manager.registration_activation_error",
                plugin_id=record.manifest.id,
            )
            record.state = PluginState.ERROR
            record.error_message = msg
            await self._publish_error_event(
                record.instance,
                "activate_registrations",
                msg,
            )
            self._clear_plugin_registrations(record.manifest.id, record)
            return False

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
