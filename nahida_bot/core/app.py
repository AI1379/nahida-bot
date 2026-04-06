"""Main application container."""

import asyncio
import logging
import signal

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
from nahida_bot.core.exceptions import ApplicationError

logger = logging.getLogger(__name__)


class Application:
    """Main application container and lifecycle manager."""

    def __init__(self, settings: Settings | None = None) -> None:
        """Initialize the application.

        Args:
            settings: Application settings. If None, will be loaded automatically.
        """
        self.settings = settings or load_settings()
        self._initialized = False
        self._started = False
        self._shutdown_event = asyncio.Event()
        self.event_bus = EventBus(
            EventContext(app=self, settings=self.settings, logger=logger)
        )

    async def initialize(self) -> None:
        """Initialize application components."""
        if self._initialized:
            logger.warning("Application already initialized")
            return

        try:
            logger.info(
                f"Initializing application: {self.settings.app_name} "
                f"(debug={self.settings.debug})"
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
            raise ApplicationError(f"Failed to initialize application: {e}") from e

    async def start(self) -> None:
        """Start the application."""
        if not self._initialized:
            await self.initialize()

        if self._started:
            logger.warning("Application already started")
            return

        try:
            logger.info("Starting application...")
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
            logger.info("Application started successfully")
        except Exception as e:
            raise ApplicationError(f"Failed to start application: {e}") from e

    async def stop(self) -> None:
        """Stop the application gracefully."""
        if not self._started:
            logger.warning("Application not started")
            self._shutdown_event.set()
            return

        try:
            logger.info("Stopping application...")
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
            logger.info("Application stopped successfully")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
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
                    f"Signal handlers not supported for {sig}, shutdown may not work properly"
                )
                continue

        try:
            await self._shutdown_event.wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("Received interrupt signal")
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
