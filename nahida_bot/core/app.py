"""Main application container."""

from __future__ import annotations

import asyncio
import importlib
import signal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from nahida_bot.core.channel_registry import ChannelRegistry
from nahida_bot.core.config import Settings, load_settings
from nahida_bot.core.events import (
    AppInitializing,
    AppLifecyclePayload,
    AppStarted,
    AppStopped,
    AppStopping,
    EventBus,
    EventContext,
)
from nahida_bot.core.exceptions import ApplicationError, StartupError
from nahida_bot.core.logging import configure_logging
from nahida_bot.core.router import MessageRouter, RouterConfig
from nahida_bot.plugins.commands import CommandMatcher

if TYPE_CHECKING:
    from nahida_bot.agent.loop import AgentLoop
    from nahida_bot.agent.memory.store import MemoryStore
    from nahida_bot.agent.providers import ModelCapabilities
    from nahida_bot.agent.providers.manager import ProviderManager
    from nahida_bot.core.session_runner import SessionRunner
    from nahida_bot.db.engine import DatabaseEngine
    from nahida_bot.plugins.manager import PluginManager
    from nahida_bot.scheduler.service import SchedulerService
    from nahida_bot.workspace.manager import WorkspaceManager

logger = structlog.get_logger(__name__)


class Application:
    """Main application container and lifecycle manager."""

    def __init__(self, settings: Settings | None = None) -> None:
        """Initialize the application.

        Args:
            settings: Application settings. If None, will be loaded automatically.
        """
        self.settings = settings or load_settings()
        configure_logging(
            debug=self.settings.debug,
            log_level=self.settings.log_level,
            log_json=self.settings.log_json,
        )
        self._initialized = False
        self._started = False
        self._shutdown_event: asyncio.Event | None = None
        self.event_bus = EventBus(
            EventContext(app=self, settings=self.settings, logger=logger)
        )
        self.channel_registry = ChannelRegistry()
        self.plugin_manager: PluginManager | None = None
        self.message_router: MessageRouter | None = None
        self.agent_loop: AgentLoop | None = None
        self.memory_store: MemoryStore | None = None
        self.workspace_manager: WorkspaceManager | None = None
        self._db_engine: DatabaseEngine | None = None
        self._provider_manager: ProviderManager | None = None
        self._providers_to_close: list[object] = []  # ChatProvider instances
        self.session_runner: SessionRunner | None = None
        self.scheduler_service: SchedulerService | None = None

        logger.debug(
            "application.instance_created",
            app_name=self.settings.app_name,
            settings=self.settings.model_dump(),
        )

    async def initialize(self) -> None:
        """Initialize application components."""
        if self._initialized:
            logger.warning("application.already_initialized")
            return

        try:
            logger.info(
                "application.initializing",
                app_name=self.settings.app_name,
                debug=self.settings.debug,
            )
            result = await self.event_bus.publish(
                AppInitializing(
                    payload=AppLifecyclePayload(
                        app_name=self.settings.app_name,
                        debug=self.settings.debug,
                    ),
                    source="core.app.initialize",
                )
            )
            if result.failures:
                details = "; ".join(
                    f"{f.handler_name}: {f.error}" for f in result.failures
                )
                raise StartupError(
                    f"Lifecycle handler(s) failed during init: {details}"
                )

            # Initialize plugin manager early so pre-agent plugins can register
            # provider types before ProviderManager is built.
            from nahida_bot.plugins.manager import PluginManager
            from nahida_bot.plugins.tool_executor import RegistryToolExecutor

            self.plugin_manager = PluginManager(
                event_bus=self.event_bus,
                channel_registry=self.channel_registry,
            )
            await self._discover_plugins()
            self._inject_plugin_configs()

            # Import built-in provider modules before runtime provider plugins
            # run, so type-key conflicts with built-ins are caught consistently.
            importlib.import_module("nahida_bot.agent.providers")

            await self.plugin_manager.load_all(phase="pre-agent")
            await self.plugin_manager.enable_all(phase="pre-agent")

            # Initialize database, memory, and agent subsystems
            await self._init_agent_subsystem()
            self._init_workspace_subsystem()

            self.plugin_manager.set_runtime_services(
                workspace_manager=self.workspace_manager,
                memory_store=self.memory_store,
                provider_manager=self._provider_manager,
            )
            if self.agent_loop is not None:
                self.agent_loop.tool_executor = RegistryToolExecutor(
                    self.plugin_manager.tool_registry
                )

            # Initialize scheduler
            self._init_scheduler()
            self.plugin_manager.set_runtime_services(
                workspace_manager=self.workspace_manager,
                memory_store=self.memory_store,
                provider_manager=self._provider_manager,
                scheduler_service=self.scheduler_service,
            )

            self._initialized = True
        except Exception as e:
            logger.exception(
                "application.initialize_failed",
                app_name=self.settings.app_name,
            )
            raise StartupError(f"Failed to initialize application: {e}") from e

    async def _init_agent_subsystem(self) -> None:
        """Create ProviderManager, AgentLoop, and MemoryStore."""
        from nahida_bot.agent.context import ContextBuilder, ContextBudget
        from nahida_bot.agent.loop import AgentLoop
        from nahida_bot.agent.memory.sqlite import SQLiteMemoryStore
        from nahida_bot.agent.providers import create_provider
        from nahida_bot.agent.providers.manager import ProviderManager, ProviderSlot
        from nahida_bot.db.engine import DatabaseEngine

        # Database + Memory
        db_path = self.settings.db_path
        engine = DatabaseEngine(db_path)
        await engine.initialize()
        self._db_engine = engine
        self.memory_store = SQLiteMemoryStore(engine)
        logger.info("application.memory_initialized", db_path=db_path)

        # Build providers from config
        slots: list[ProviderSlot] = []
        providers_cfg = self.settings.providers

        for pid, cfg in providers_cfg.items():
            model_entries = _provider_model_entries(cfg.models)
            if not cfg.api_key or not model_entries:
                logger.warning(
                    "application.provider_skipped",
                    provider_id=pid,
                    reason="missing api_key or models",
                )
                continue

            default_model = model_entries[0][0]
            available_models = [name for name, _ in model_entries]
            capabilities_by_model = {
                name: _model_capabilities_from_config(raw)
                for name, raw in model_entries
            }
            provider_kwargs: dict[str, object] = {
                "base_url": cfg.base_url,
                "api_key": cfg.api_key,
                "model": default_model,
            }
            merge_flag = getattr(cfg, "merge_system_messages", None)
            if merge_flag is not None:
                provider_kwargs["merge_system_messages"] = merge_flag
            for extra_field in (
                "max_output_tokens",
                "store_responses",
                "use_previous_response_id",
                "stream_responses",
                "reasoning_effort",
                "built_in_tools",
            ):
                value = getattr(cfg, extra_field, None)
                if value is not None:
                    provider_kwargs[extra_field] = value
            provider = create_provider(cfg.type, **provider_kwargs)
            cb = ContextBuilder(budget=ContextBudget(), provider=provider)
            slots.append(
                ProviderSlot(
                    id=pid,
                    provider=provider,
                    context_builder=cb,
                    default_model=default_model,
                    available_models=available_models,
                    capabilities_by_model=capabilities_by_model,
                )
            )
            self._providers_to_close.append(provider)
            logger.info(
                "application.provider_initialized",
                provider_id=pid,
                provider_type=cfg.type,
                model=default_model,
            )

        if slots:
            default_id = self.settings.default_provider or ""
            self._provider_manager = ProviderManager(slots, default_id=default_id)
            # Create a single AgentLoop with the default provider as fallback
            default_slot = self._provider_manager.default or slots[0]
            self.agent_loop = AgentLoop(
                provider=default_slot.provider,
                context_builder=default_slot.context_builder,
            )
        else:
            logger.warning(
                "application.no_provider",
                msg="Provider not configured — agent loop disabled. "
                "Set providers.<id>.api_key and providers.<id>.models in config.",
            )

    def _init_workspace_subsystem(self) -> None:
        """Create and initialize the active workspace manager."""
        from nahida_bot.workspace.manager import WorkspaceManager

        manager = WorkspaceManager(Path(self.settings.workspace_base_dir))
        metadata = manager.initialize()
        self.workspace_manager = manager
        logger.info(
            "application.workspace_initialized",
            workspace_id=metadata.workspace_id,
            path=str(manager.workspace_path(metadata.workspace_id)),
        )

    def _init_scheduler(self) -> None:
        """Create the SessionRunner and SchedulerService."""
        from pathlib import Path

        from nahida_bot.agent.media.cache import MediaCache
        from nahida_bot.agent.media.resolver import MediaPolicy, MediaResolver
        from nahida_bot.core.session_runner import SessionRunner
        from nahida_bot.scheduler.repository import CronRepository
        from nahida_bot.scheduler.service import SchedulerService

        if self._db_engine is None:
            return

        tool_registry = (
            self.plugin_manager.tool_registry
            if self.plugin_manager is not None
            else None
        )

        # Build media infrastructure from multimodal config
        multimodal = self.settings.multimodal
        cache_dir = str(Path(self.settings.db_path).parent / "media_cache")
        media_cache = MediaCache(
            cache_dir, ttl_seconds=multimodal.media_cache_ttl_seconds
        )
        media_policy = MediaPolicy(
            max_image_bytes=multimodal.max_image_bytes,
            supported_mime_types=("image/jpeg", "image/png", "image/webp"),
            max_images_per_turn=multimodal.max_images_per_turn,
            cache_ttl_seconds=multimodal.media_cache_ttl_seconds,
            cache_dir=cache_dir,
        )
        media_resolver = MediaResolver(cache=media_cache, policy=media_policy)

        self.session_runner = SessionRunner(
            agent_loop=self.agent_loop,
            memory_store=self.memory_store,
            provider_manager=self._provider_manager,
            workspace_manager=self.workspace_manager,
            tool_registry=tool_registry,
            multimodal_config=multimodal,
            media_resolver=media_resolver,
            channel_registry=self.channel_registry,
        )

        # Register image_understand tool when fallback mode is "tool"
        if multimodal.image_fallback_mode == "tool" and tool_registry is not None:
            from nahida_bot.plugins.registry import ToolEntry

            if tool_registry.get("image_understand") is None:
                tool_registry.register(
                    ToolEntry(
                        name="image_understand",
                        description=(
                            "Analyze an image attached to the current conversation. "
                            "Returns a detailed description, any visible text (OCR), "
                            "and safety observations."
                        ),
                        parameters={
                            "type": "object",
                            "properties": {
                                "media_id": {
                                    "type": "string",
                                    "description": (
                                        "The media ID of the image to analyze. "
                                        "Use 'latest' for the most recent image."
                                    ),
                                },
                                "question": {
                                    "type": "string",
                                    "description": (
                                        "Optional specific question about the image."
                                    ),
                                },
                            },
                            "required": ["media_id"],
                            "additionalProperties": False,
                        },
                        handler=self.session_runner.handle_image_understand_tool,
                        plugin_id="builtin",
                    )
                )

        repo = CronRepository(self._db_engine)
        self.scheduler_service = SchedulerService(
            repo,
            runner=self.session_runner,
            channel_registry=self.channel_registry,
            system_prompt=self.settings.system_prompt,
        )
        if self.plugin_manager is not None:
            self.plugin_manager.scheduler_service = self.scheduler_service
        logger.info("application.scheduler_initialized")

    def _get_plugin_configs(self) -> dict[str, dict[str, Any]]:
        """Extract plugin-specific configs from settings.

        Settings with ``extra="allow"`` stores arbitrary top-level keys.
        Any key that doesn't match a known Settings field is treated as
        plugin config. The key name must match a plugin id.
        """
        known_fields = set(Settings.model_fields.keys())
        plugin_configs: dict[str, dict[str, Any]] = {}
        for key, value in self.settings.model_dump().items():
            if key not in known_fields and isinstance(value, dict):
                plugin_configs[key] = value
        return plugin_configs

    async def _discover_plugins(self) -> None:
        """Discover builtin and user plugins without loading them."""
        if self.plugin_manager is None:
            return

        # Discover builtin commands from nahida_bot/plugins/builtin/
        try:
            import nahida_bot.plugins.builtin as builtin_pkg

            builtin_file = builtin_pkg.__file__
            if builtin_file is not None:
                builtin_path = Path(builtin_file).parent
                await self.plugin_manager.discover([builtin_path])
        except ImportError:
            pass

        # Discover builtin channels from nahida_bot/channels/
        if self.settings.discover_builtin_channels:
            try:
                import nahida_bot.channels as channels_pkg

                channels_file = channels_pkg.__file__
                if channels_file is not None:
                    builtin_channels_path = Path(channels_file).parent
                    await self.plugin_manager.discover([builtin_channels_path])
            except ImportError:
                pass

        plugin_paths = [Path(p).resolve() for p in self.settings.plugin_paths]
        await self.plugin_manager.discover(plugin_paths)

    def _inject_plugin_configs(self) -> None:
        """Merge config.yaml top-level plugin config into discovered manifests."""
        if self.plugin_manager is None:
            return

        plugin_configs = self._get_plugin_configs()
        for record in self.plugin_manager.list_plugins():
            plugin_id = record.manifest.id
            if plugin_id in plugin_configs:
                existing = record.manifest.config or {}
                merged = {**existing, **plugin_configs[plugin_id]}
                record.manifest = record.manifest.model_copy(update={"config": merged})

    async def start(self) -> None:
        """Start the application."""
        if not self._initialized:
            await self.initialize()

        if self._started:
            logger.warning("application.already_started")
            return

        try:
            logger.info(
                "application.starting",
                app_name=self.settings.app_name,
            )
            if self.plugin_manager is not None:
                await self.plugin_manager.load_all(phase="post-agent")
                await self.plugin_manager.enable_all(phase="post-agent")

            # Create and start the message router
            assert self.plugin_manager is not None
            self.message_router = MessageRouter(
                event_bus=self.event_bus,
                command_registry=self.plugin_manager.command_registry,
                command_matcher=CommandMatcher(),
                channel_registry=self.channel_registry,
                runner=self.session_runner,
                workspace_manager=self.workspace_manager,
                config=RouterConfig(
                    system_prompt=self.settings.system_prompt,
                ),
            )
            await self.message_router.start()

            # Start scheduler (after router, so it can resolve sessions)
            if self.scheduler_service is not None:
                # Update the shared runner with the final tool registry
                if self.session_runner is not None:
                    self.session_runner.tool_registry = (
                        self.plugin_manager.tool_registry
                    )
                self.scheduler_service.wire_runtime(
                    message_router=self.message_router,
                )
                await self.scheduler_service.start()

            result = await self.event_bus.publish(
                AppStarted(
                    payload=AppLifecyclePayload(
                        app_name=self.settings.app_name,
                        debug=self.settings.debug,
                    ),
                    source="core.app.start",
                )
            )
            if result.failures:
                details = "; ".join(
                    f"{f.handler_name}: {f.error}" for f in result.failures
                )
                raise StartupError(
                    f"Lifecycle handler(s) failed during start: {details}"
                )
            self._started = True
            logger.info(
                "application.started",
                app_name=self.settings.app_name,
            )
        except Exception as e:
            logger.exception(
                "application.start_failed",
                app_name=self.settings.app_name,
            )
            raise StartupError(f"Failed to start application: {e}") from e

    async def stop(self) -> None:
        """Stop the application gracefully."""
        was_started = self._started
        try:
            if not was_started:
                logger.warning("application.stop_without_start")
                if self.plugin_manager is not None:
                    await self.plugin_manager.shutdown_all()
            else:
                logger.info(
                    "application.stopping",
                    app_name=self.settings.app_name,
                )
                await self.event_bus.publish(
                    AppStopping(
                        payload=AppLifecyclePayload(
                            app_name=self.settings.app_name,
                            debug=self.settings.debug,
                        ),
                        source="core.app.stop",
                    )
                )
                # Shut down scheduler before message router
                if self.scheduler_service is not None:
                    await self.scheduler_service.stop()

                # Shut down message router before plugins
                if self.message_router is not None:
                    await self.message_router.stop()

                # Shut down plugins before event bus
                if self.plugin_manager is not None:
                    await self.plugin_manager.shutdown_all()

                self._started = False

                await self.event_bus.publish(
                    AppStopped(
                        payload=AppLifecyclePayload(
                            app_name=self.settings.app_name,
                            debug=self.settings.debug,
                        ),
                        source="core.app.stop",
                    )
                )
                await self.event_bus.shutdown(timeout=1.0)

            # Always clean up resources, even if startup didn't fully complete.
            for provider in self._providers_to_close:
                close_fn = getattr(provider, "close", None)
                if close_fn is not None:
                    await close_fn()
            self._providers_to_close.clear()
            if self._db_engine is not None:
                await self._db_engine.close()
                self._db_engine = None

            if self._shutdown_event is not None:
                self._shutdown_event.set()
            logger.info(
                "application.stopped",
                app_name=self.settings.app_name,
            )
        except Exception as e:
            logger.exception(
                "application.stop_failed",
                app_name=self.settings.app_name,
            )
            raise ApplicationError(f"Failed to stop application: {e}") from e

    def request_shutdown(self) -> None:
        """Request application shutdown from external callers."""
        if self._shutdown_event is not None:
            self._shutdown_event.set()

    async def run(self) -> None:
        """Run the application until interrupted."""
        self._shutdown_event = asyncio.Event()
        await self.start()

        loop = asyncio.get_running_loop()
        registered_signals: list[signal.Signals] = []
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.request_shutdown)
                registered_signals.append(sig)
            except (NotImplementedError, RuntimeError):
                # Signal handlers may be unavailable on some platforms/runtimes.
                logger.debug(
                    "application.signal_handler_unavailable",
                    signal=str(sig),
                )
                continue

        try:
            await self._shutdown_event.wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("application.interrupt_received")
        finally:
            for sig in registered_signals:
                loop.remove_signal_handler(sig)
            # Shield stop() from a second KeyboardInterrupt during cleanup.
            try:
                await asyncio.shield(self.stop())
            except (KeyboardInterrupt, asyncio.CancelledError):
                # On Windows a second Ctrl+C during shutdown is common.
                # Force best-effort cleanup.
                try:
                    await self.stop()
                except Exception:  # noqa: BLE001
                    pass

    @property
    def is_initialized(self) -> bool:
        """Check if application is initialized."""
        return self._initialized

    @property
    def is_started(self) -> bool:
        """Check if application is started."""
        return self._started


def _model_capabilities_from_config(raw: dict[str, Any]) -> "ModelCapabilities":
    """Create ModelCapabilities from config, ignoring unknown keys."""
    from nahida_bot.agent.providers import ModelCapabilities

    if not raw:
        return ModelCapabilities()
    allowed = set(ModelCapabilities.__dataclass_fields__.keys())
    values = {key: value for key, value in raw.items() if key in allowed}
    if "supported_image_mime_types" in values and isinstance(
        values["supported_image_mime_types"], list
    ):
        values["supported_image_mime_types"] = tuple(
            str(item) for item in values["supported_image_mime_types"]
        )
    return ModelCapabilities(**values)


def _provider_model_entries(
    raw_models: list[Any],
) -> list[tuple[str, dict[str, Any]]]:
    """Normalize provider model config into ``(model_name, capabilities)`` pairs."""
    from nahida_bot.core.config import ProviderModelConfig

    entries: list[tuple[str, dict[str, Any]]] = []
    for raw in raw_models:
        if isinstance(raw, str):
            name = raw.strip()
            if name:
                entries.append((name, {}))
            continue
        if isinstance(raw, ProviderModelConfig):
            name = raw.name.strip()
            if name:
                entries.append((name, raw.capabilities))
    return entries
