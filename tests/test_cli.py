"""Tests for CLI module."""

from typer.testing import CliRunner

from nahida_bot.cli import app

runner = CliRunner()


class TestCLIVersion:
    """Test version command."""

    def test_version_command(self) -> None:
        """Test version output."""
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert "Nahida Bot" in result.stdout
        assert "0.1.0" in result.stdout


class TestCLIConfig:
    """Test config command."""

    def test_config_command(self) -> None:
        """Test config output."""
        result = runner.invoke(app, ["config"])
        assert result.exit_code == 0
        assert "Current Configuration" in result.stdout
        assert "App Name" in result.stdout
        assert "Debug" in result.stdout

    def test_config_shows_settings(self) -> None:
        """Test that config shows expected settings."""
        result = runner.invoke(app, ["config"])
        assert "Nahida Bot" in result.stdout
        assert "6185" in result.stdout


class TestCLIDoctor:
    """Test doctor command."""

    def test_doctor_command(self) -> None:
        """Test doctor diagnostics."""
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "Diagnostic Report" in result.stdout
        assert "✓" in result.stdout or "Pass" in result.stdout

    def test_doctor_passes_checks(self) -> None:
        """Test that all checks pass in doctor."""
        result = runner.invoke(app, ["doctor"])
        assert "All checks passed" in result.stdout


class TestCLIHelp:
    """Test help text."""

    def test_help_command(self) -> None:
        """Test help output."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Usage" in result.stdout
        assert "Commands" in result.stdout

    def test_version_help(self) -> None:
        """Test version command help."""
        result = runner.invoke(app, ["version", "--help"])
        assert result.exit_code == 0
        assert "Show version information" in result.stdout

    def test_config_help(self) -> None:
        """Test config command help."""
        result = runner.invoke(app, ["config", "--help"])
        assert result.exit_code == 0
        assert "Show current configuration" in result.stdout

    def test_doctor_help(self) -> None:
        """Test doctor command help."""
        result = runner.invoke(app, ["doctor", "--help"])
        assert result.exit_code == 0
        assert "diagnostic" in result.stdout.lower()
