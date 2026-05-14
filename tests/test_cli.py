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
        """Test config schema output."""
        result = runner.invoke(
            app, ["config", "schema", "--section", "app_name", "--format", "json"]
        )
        assert result.exit_code == 0
        assert '"path": "app_name"' in result.stdout
        assert '"default": "Nahida Bot"' in result.stdout

    def test_config_shows_settings(self) -> None:
        """Test config schema includes expected settings."""
        result = runner.invoke(
            app, ["config", "schema", "--section", "port", "--format", "json"]
        )
        assert result.exit_code == 0
        assert '"path": "port"' in result.stdout
        assert '"default": "6185"' in result.stdout


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
        assert "Configuration management" in result.stdout
        assert "schema" in result.stdout
        assert "validate" in result.stdout

    def test_doctor_help(self) -> None:
        """Test doctor command help."""
        result = runner.invoke(app, ["doctor", "--help"])
        assert result.exit_code == 0
        assert "diagnostic" in result.stdout.lower()
