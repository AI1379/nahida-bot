"""Main application container."""

import asyncio
import signal

import structlog

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
        self._shutdown_event = asyncio.Event()
        self.event_bus = EventBus(
            EventContext(app=self, settings=self.settings, logger=logger)
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
            await self.event_bus.publish(
                AppInitializing(
                    payload=AppLifecyclePayload(
                        app_name=self.settings.app_name,
                        debug=self.settings.debug,
                    ),
                    source="core.app.initialize",
                )
            )
            # TODO: Placeholder for future initialization logic
            self._initialized = True
        except Exception as e:
            logger.exception(
                "application.initialize_failed",
                app_name=self.settings.app_name,
            )
            raise StartupError(f"Failed to initialize application: {e}") from e

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
            # TODO: Placeholder for future startup logic
            self._started = True
            await self.event_bus.publish(
                AppStarted(
                    payload=AppLifecyclePayload(
                        app_name=self.settings.app_name,
                        debug=self.settings.debug,
                    ),
                    source="core.app.start",
                )
            )
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
            logger.warning("application.stop_without_start")
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
            # TODO: Placeholder for future shutdown logic
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
        self._shutdown_event.set()

    async def run(self) -> None:
        """Run the application until interrupted."""
        await self.start()
        self._shutdown_event.clear()

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
