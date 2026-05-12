"""Integration tests for Phase 0."""

import pytest

from nahida_bot.core.app import Application
from nahida_bot.core.config import Settings


class TestPhase0Foundation:
    """Integration tests for Phase 0 project foundation."""

    def test_project_structure(self) -> None:
        """Test that all required modules can be imported."""
        # Core module
        from nahida_bot.core import exceptions  # noqa: F401  # pyright: ignore[reportUnusedImport]

        # Submodules
        from nahida_bot import workspace  # noqa: F401  # pyright: ignore[reportUnusedImport]
        from nahida_bot import agent  # noqa: F401  # pyright: ignore[reportUnusedImport]
        from nahida_bot import plugins  # noqa: F401  # pyright: ignore[reportUnusedImport]
        from nahida_bot import gateway  # noqa: F401  # pyright: ignore[reportUnusedImport]
        from nahida_bot import node  # noqa: F401  # pyright: ignore[reportUnusedImport]
        from nahida_bot import db  # noqa: F401  # pyright: ignore[reportUnusedImport]
        from nahida_bot import cli  # noqa: F401  # pyright: ignore[reportUnusedImport]

        # All imports should succeed
        assert True

    @pytest.mark.asyncio
    async def test_application_lifecycle_integration(self) -> None:
        """Test complete application lifecycle."""
        settings = Settings(
            app_name="Integration Test Bot",
            debug=True,
            db_path=":memory:",
        )

        app = Application(settings=settings)

        # Initialize
        await app.initialize()
        assert app.is_initialized

        # Start
        await app.start()
        assert app.is_started

        # Stop
        await app.stop()
        assert not app.is_started

    @pytest.mark.asyncio
    async def test_settings_integration(self) -> None:
        """Test settings integration with application."""
        settings = Settings(
            app_name="Settings Test",
            debug=False,
            host="0.0.0.0",
            port=9999,
        )

        app = Application(settings=settings)
        await app.initialize()

        assert app.settings.app_name == "Settings Test"
        assert app.settings.debug is False
        assert app.settings.host == "0.0.0.0"
        assert app.settings.port == 9999

    def test_version_available(self) -> None:
        """Test that version is accessible."""
        from nahida_bot import __version__

        assert __version__ == "0.1.0"
        assert isinstance(__version__, str)

    def test_cli_available(self) -> None:
        """Test that CLI is accessible."""
        from nahida_bot.cli import app, main

        assert app is not None
        assert callable(main)

    @pytest.mark.asyncio
    async def test_exception_handling(self) -> None:
        """Test exception hierarchy."""
        from nahida_bot.core.exceptions import (
            ApplicationError,
            ConfigError,
            NahidaBotError,
        )

        # Test exception catching
        try:
            raise ApplicationError("Test error")
        except NahidaBotError:
            # Should catch as NahidaBotError
            assert True

        try:
            raise ConfigError("Config error")
        except NahidaBotError:
            # Should catch as NahidaBotError
            assert True
