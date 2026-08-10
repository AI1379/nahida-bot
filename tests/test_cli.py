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

    def test_doctor_command(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """Test doctor diagnostics in an isolated working directory."""
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "Diagnostic Report" in result.stdout
        assert "PASS" in result.stdout

    def test_doctor_passes_checks(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """Test that doctor reports no blocking issues in a clean cwd."""
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["doctor"])
        assert "No blocking issues found" in result.stdout


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
