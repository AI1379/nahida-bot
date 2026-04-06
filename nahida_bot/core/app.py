"""Main application container."""

import asyncio
import logging

from nahida_bot.core.config import Settings, load_settings
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
            # Placeholder for future initialization logic
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
            # Placeholder for future startup logic
            self._started = True
            logger.info("Application started successfully")
        except Exception as e:
            raise ApplicationError(f"Failed to start application: {e}") from e

    async def stop(self) -> None:
        """Stop the application gracefully."""
        if not self._started:
            logger.warning("Application not started")
            return

        try:
            logger.info("Stopping application...")
            # Placeholder for future shutdown logic
            self._started = False
            logger.info("Application stopped successfully")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
            raise ApplicationError(f"Failed to stop application: {e}") from e

    async def run(self) -> None:
        """Run the application until interrupted."""
        await self.start()
        try:
            # Keep the application running
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("Received interrupt signal")
        finally:
            await self.stop()

    @property
    def is_initialized(self) -> bool:
        """Check if application is initialized."""
        return self._initialized

    @property
    def is_started(self) -> bool:
        """Check if application is started."""
        return self._started
