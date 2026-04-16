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
    from nahida_bot.plugins.manager import PluginManager

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

            # Initialize plugin manager
            from nahida_bot.plugins.manager import PluginManager

            self.plugin_manager = PluginManager(
                event_bus=self.event_bus,
                workspace_manager=None,
                memory_store=self.memory_store,
                channel_registry=self.channel_registry,
            )

            self._initialized = True
        except Exception as e:
            logger.exception(
                "application.initialize_failed",
                app_name=self.settings.app_name,
            )
            raise StartupError(f"Failed to initialize application: {e}") from e

    async def _init_agent_subsystem(self) -> None:
        """Create Provider, AgentLoop, and MemoryStore."""
        from nahida_bot.agent.context import ContextBuilder, ContextBudget
        from nahida_bot.agent.loop import AgentLoop
        from nahida_bot.agent.memory.sqlite import SQLiteMemoryStore
        from nahida_bot.agent.providers import create_provider
        from nahida_bot.db.engine import DatabaseEngine

        # Database + Memory
        db_path = self.settings.db_path
        engine = DatabaseEngine(db_path)
        await engine.initialize()
        self.memory_store = SQLiteMemoryStore(engine)
        logger.info("application.memory_initialized", db_path=db_path)

        # Provider (LLM backend) — only if configured
        provider_cfg = self.settings.provider
        if provider_cfg.api_key and provider_cfg.model:
            provider = create_provider(
                provider_cfg.type,
                base_url=provider_cfg.base_url,
                api_key=provider_cfg.api_key,
                model=provider_cfg.model,
            )
            context_builder = ContextBuilder(
                budget=ContextBudget(),
                provider=provider,
            )
            self.agent_loop = AgentLoop(
                provider=provider,
                context_builder=context_builder,
            )
            logger.info(
                "application.provider_initialized",
                provider_type=provider_cfg.type,
                model=provider_cfg.model,
            )
        else:
            logger.warning(
                "application.no_provider",
                msg="Provider not configured — agent loop disabled. "
                "Set provider.api_key and provider.model in config.",
            )

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
                # Discover builtin channels from nahida_bot/channels/
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
                agent_loop=self.agent_loop,
                memory_store=self.memory_store,
                config=RouterConfig(
                    system_prompt=self.settings.system_prompt,
                ),
            )
            await self.message_router.start()

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
        if not self._started:
            # FIXME: This branch currently skips plugin shutdown entirely.
            # Consider cleaning up loaded/enabled plugins even when startup
            # failed before setting _started = True.
            logger.warning("application.stop_without_start")
            if self._shutdown_event is not None:
                self._shutdown_event.set()
            return

        try:
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
                logger.warning(
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
            await self.stop()

    @property
    def is_initialized(self) -> bool:
        """Check if application is initialized."""
        return self._initialized

    @property
    def is_started(self) -> bool:
        """Check if application is started."""
        return self._started
