"""Tests for core module."""

import asyncio
import logging
from pathlib import Path

import pytest
import structlog

from nahida_bot.core.app import Application
from nahida_bot.core.config import Settings, load_settings
from nahida_bot.core.exceptions import (
    ApplicationError,
    CommunicationError,
    ConfigError,
    NahidaBotError,
    StartupError,
)


class TestExceptions:
    """Test exception hierarchy."""

    def test_nahida_bot_error_is_exception(self) -> None:
        """Test that NahidaBotError is an Exception."""
        assert issubclass(NahidaBotError, Exception)

    def test_config_error_inherits_from_nahida_bot_error(self) -> None:
        """Test ConfigError inherits from NahidaBotError."""
        assert issubclass(ConfigError, NahidaBotError)

    def test_application_error_inherits_from_nahida_bot_error(self) -> None:
        """Test ApplicationError inherits from NahidaBotError."""
        assert issubclass(ApplicationError, NahidaBotError)

    def test_raise_config_error(self) -> None:
        """Test raising ConfigError."""
        with pytest.raises(ConfigError):
            raise ConfigError("Test config error")

    def test_raise_application_error(self) -> None:
        """Test raising ApplicationError."""
        with pytest.raises(ApplicationError):
            raise ApplicationError("Test app error")

    def test_startup_error_inherits_application_error(self) -> None:
        """Test StartupError inherits from ApplicationError."""
        assert issubclass(StartupError, ApplicationError)

    def test_communication_error_inherits_application_error(self) -> None:
        """Test CommunicationError inherits from ApplicationError."""
        assert issubclass(CommunicationError, ApplicationError)


class TestSettings:
    """Test configuration settings."""

    def test_default_settings(self) -> None:
        """Test loading default settings."""
        settings = load_settings()
        assert isinstance(settings, Settings)
        assert settings.app_name == "Nahida Bot"
        assert settings.debug is False
        assert settings.log_level == "INFO"
        assert settings.log_json is None
        assert settings.log_file is None
        assert settings.log_file_level is None
        assert settings.log_file_json is True
        assert settings.port == 6185

    def test_custom_settings(self) -> None:
        """Test creating custom settings."""
        settings = Settings(
            app_name="Custom Bot",
            debug=True,
            log_level="DEBUG",
            log_json=False,
            log_file="./data/logs/nahida.log",
            log_file_level="DEBUG",
            log_file_json=True,
            host="0.0.0.0",
            port=8000,
        )
        assert settings.app_name == "Custom Bot"
        assert settings.debug is True
        assert settings.log_level == "DEBUG"
        assert settings.log_json is False
        assert settings.log_file == "./data/logs/nahida.log"
        assert settings.log_file_level == "DEBUG"
        assert settings.log_file_json is True
        assert settings.host == "0.0.0.0"
        assert settings.port == 8000

    def test_settings_with_db_path(self) -> None:
        """Test settings with custom database path."""
        settings = Settings(db_path="/custom/path/db.sqlite")
        assert settings.db_path == "/custom/path/db.sqlite"


class TestLogging:
    """Test logging configuration."""

    def test_file_handler_can_capture_debug_without_console_noise(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """File handler can have a lower level than the console handler."""
        from nahida_bot.core import logging as logging_config

        monkeypatch.setattr(logging_config, "_configured", False)
        log_file = tmp_path / "nahida.log"

        try:
            logging_config.configure_logging(
                debug=False,
                log_level="INFO",
                log_file=str(log_file),
                log_file_level="DEBUG",
            )

            logger = structlog.get_logger("nahida_bot.tests.logging")
            logger.debug("debug.only_in_file", answer=42)
            logger.info("info.in_both", answer=43)

            for handler in logging.getLogger().handlers:
                handler.flush()

            captured = capsys.readouterr()
            console_output = captured.out + captured.err
            file_output = log_file.read_text(encoding="utf-8")

            assert "debug.only_in_file" not in console_output
            assert "info.in_both" in console_output
            assert "debug.only_in_file" in file_output
            assert "info.in_both" in file_output
        finally:
            monkeypatch.setattr(logging_config, "_configured", False)
            for handler in list(logging.getLogger().handlers):
                if getattr(handler, logging_config._HANDLER_ATTR, False):
                    logging.getLogger().removeHandler(handler)
                    handler.close()


class TestApplication:
    """Test Application lifecycle."""

    @pytest.mark.asyncio
    async def test_application_initialization(self, app: Application) -> None:
        """Test application initialization."""
        assert app.is_initialized is True

    @pytest.mark.asyncio
    async def test_application_start_stop(self, test_settings: Settings) -> None:
        """Test starting and stopping application."""
        application = Application(settings=test_settings)
        await application.initialize()

        assert application.is_initialized is True
        assert application.is_started is False

        await application.start()
        assert application.is_started is True

        await application.stop()
        assert application.is_started is False

    @pytest.mark.asyncio
    async def test_application_double_initialize(self, app: Application) -> None:
        """Test that double initialization is safe."""
        # Should log warning but not raise
        await app.initialize()
        assert app.is_initialized is True

    @pytest.mark.asyncio
    async def test_application_double_start(self, app: Application) -> None:
        """Test that double start is safe."""
        await app.start()
        # Should log warning but not raise
        await app.start()
        assert app.is_started is True
        await app.stop()

    @pytest.mark.asyncio
    async def test_application_stop_when_not_started(self, app: Application) -> None:
        """Test stopping application that was never started."""
        # Should complete without error
        await app.stop()
        assert app.is_started is False

    @pytest.mark.asyncio
    async def test_application_properties(self, test_settings: Settings) -> None:
        """Test application properties."""
        application = Application(settings=test_settings)
        assert application.settings.app_name == "Test Bot"
        assert application.settings.debug is True

    @pytest.mark.asyncio
    async def test_application_run_stops_on_shutdown_request(
        self, test_settings: Settings
    ) -> None:
        """Test run loop exits when shutdown is requested."""
        application = Application(settings=test_settings)
        run_task = asyncio.create_task(application.run())

        await asyncio.sleep(0)
        application.request_shutdown()

        await asyncio.wait_for(run_task, timeout=1)
        assert application.is_started is False
