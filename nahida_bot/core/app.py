"""Main application container."""

from __future__ import annotations

import asyncio
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

            # Initialize database, memory, and agent subsystems
            await self._init_agent_subsystem()
            self._init_workspace_subsystem()

            # Initialize plugin manager
            from nahida_bot.plugins.manager import PluginManager
            from nahida_bot.plugins.tool_executor import RegistryToolExecutor

            self.plugin_manager = PluginManager(
                event_bus=self.event_bus,
                workspace_manager=self.workspace_manager,
                memory_store=self.memory_store,
                channel_registry=self.channel_registry,
                provider_manager=self._provider_manager,
            )
            if self.agent_loop is not None:
                self.agent_loop.tool_executor = RegistryToolExecutor(
                    self.plugin_manager.tool_registry
                )

            # Initialize scheduler
            self._init_scheduler()

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

        if providers_cfg:
            # Multi-provider mode
            for pid, cfg in providers_cfg.items():
                if not cfg.api_key or not cfg.model:
                    logger.warning(
                        "application.provider_skipped",
                        provider_id=pid,
                        reason="missing api_key or model",
                    )
                    continue
                provider = create_provider(
                    cfg.type,
                    base_url=cfg.base_url,
                    api_key=cfg.api_key,
                    model=cfg.model,
                )
                cb = ContextBuilder(budget=ContextBudget(), provider=provider)
                models = cfg.models or [cfg.model]
                slots.append(
                    ProviderSlot(
                        id=pid,
                        provider=provider,
                        context_builder=cb,
                        default_model=cfg.model,
                        available_models=models,
                    )
                )
                self._providers_to_close.append(provider)
                logger.info(
                    "application.provider_initialized",
                    provider_id=pid,
                    provider_type=cfg.type,
                    model=cfg.model,
                )
        else:
            # Legacy single-provider mode
            provider_cfg = self.settings.provider
            if provider_cfg.api_key and provider_cfg.model:
                provider = create_provider(
                    provider_cfg.type,
                    base_url=provider_cfg.base_url,
                    api_key=provider_cfg.api_key,
                    model=provider_cfg.model,
                )
                cb = ContextBuilder(budget=ContextBudget(), provider=provider)
                slots.append(
                    ProviderSlot(
                        id="default",
                        provider=provider,
                        context_builder=cb,
                        default_model=provider_cfg.model,
                        available_models=[provider_cfg.model],
                    )
                )
                self._providers_to_close.append(provider)
                logger.info(
                    "application.provider_initialized",
                    provider_type=provider_cfg.type,
                    model=provider_cfg.model,
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
                "Set provider.api_key and provider.model in config.",
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

        self.session_runner = SessionRunner(
            agent_loop=self.agent_loop,
            memory_store=self.memory_store,
            provider_manager=self._provider_manager,
            workspace_manager=self.workspace_manager,
            tool_registry=tool_registry,
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
                        pass  # No builtin channels package

                # Inject plugin configs from settings into manifest config
                plugin_configs = self._get_plugin_configs()
                for record in self.plugin_manager.list_plugins():
                    plugin_id = record.manifest.id
                    if plugin_id in plugin_configs:
                        existing = record.manifest.config or {}
                        merged = {**existing, **plugin_configs[plugin_id]}
                        record.manifest = record.manifest.model_copy(
                            update={"config": merged}
                        )

                # Discover user plugins
                plugin_paths = [Path(p).resolve() for p in self.settings.plugin_paths]
                await self.plugin_manager.discover(plugin_paths)

                await self.plugin_manager.load_all()
                await self.plugin_manager.enable_all()
                # FIXME: If startup fails after plugins are enabled but before
                # self._started becomes True, stop() will early-return and skip
                # plugin_manager.shutdown_all(), leaving partial startup state.

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
