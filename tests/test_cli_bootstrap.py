"""Tests for the bootstrap CLI command."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from nahida_bot.cli import app

runner = CliRunner()


def _run(args: list[str]) -> object:
    return runner.invoke(app, args)


class TestBootstrapNonInteractive:
    def test_generates_minimal_config_and_env(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        env = tmp_path / ".env"
        result = _run(
            [
                "bootstrap",
                "--non-interactive",
                "--config-yaml",
                str(cfg),
                "--env",
                str(env),
            ]
        )
        assert result.exit_code == 0, result.output
        assert cfg.is_file()
        assert env.is_file()

        data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        assert "providers" in data
        assert "main" in data["providers"]
        assert data["providers"]["main"]["api_key"] == "${DEEPSEEK_LLM_API_KEY:}"
        assert data["default_provider"] == "main"
        # framework defaults filled in
        assert data["db_path"] == "./data/nahida.db"
        assert data["plugin_paths"] == ["./plugins"]

    def test_channels_via_env(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("NAHIDA_BOOTSTRAP_CHANNELS", "telegram,onebot")
        cfg = tmp_path / "config.yaml"
        env = tmp_path / ".env"
        result = _run(
            [
                "bootstrap",
                "--non-interactive",
                "--config-yaml",
                str(cfg),
                "--env",
                str(env),
            ]
        )
        assert result.exit_code == 0, result.output
        data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        assert data["telegram"]["enabled"] is True
        assert data["onebot"]["enabled"] is True
        # telegram secret placeholder present in .env
        assert "TELEGRAM_BOT_TOKEN" in env.read_text(encoding="utf-8")

    def test_fix_missing_preserves_existing(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        env = tmp_path / ".env"
        # First run: create a config
        first = _run(
            [
                "bootstrap",
                "--non-interactive",
                "--config-yaml",
                str(cfg),
                "--env",
                str(env),
            ]
        )
        assert first.exit_code == 0, first.output

        # Second run with --fix-missing and a different provider env: must not
        # overwrite the existing "main" provider.
        monkeypatch_env = {
            "NAHIDA_BOOTSTRAP_PROVIDER": "anthropic",
            "NAHIDA_BOOTSTRAP_PROVIDER_ID": "claude",
        }
        import os

        old = {k: os.environ.get(k) for k in monkeypatch_env}
        for k, v in monkeypatch_env.items():
            os.environ[k] = v
        try:
            result = _run(
                [
                    "bootstrap",
                    "--non-interactive",
                    "--fix-missing",
                    "--config-yaml",
                    str(cfg),
                    "--env",
                    str(env),
                ]
            )
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        assert result.exit_code == 0, result.output
        after = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        # existing deepseek "main" preserved
        assert after["providers"]["main"]["type"] == "deepseek"
        # default_provider still points at main
        assert after["default_provider"] == "main"
        # anthropic NOT added in non-interactive mode when providers exist
        assert "claude" not in after["providers"]


class TestBootstrapValidation:
    def test_generated_config_loads(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        env = tmp_path / ".env"
        first = _run(
            [
                "bootstrap",
                "--non-interactive",
                "--config-yaml",
                str(cfg),
                "--env",
                str(env),
            ]
        )
        assert first.exit_code == 0, first.output
        from nahida_bot.core.config import load_settings

        settings = load_settings(config_yaml=str(cfg), env_path=str(env))
        assert "main" in settings.providers
