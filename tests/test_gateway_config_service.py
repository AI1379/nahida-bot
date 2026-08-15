"""Tests for the Gateway config service I/O behavior."""

from __future__ import annotations

from pathlib import Path

from nahida_bot.gateway.services import config_service


class TestConfigPathResolution:
    def test_explicit_argument_wins(self, tmp_path: Path) -> None:
        cfg = tmp_path / "explicit.yaml"
        cfg.write_text("app_name: Explicit\n", encoding="utf-8")
        content = config_service.read_current_config(config_path=str(cfg))
        assert content.path == str(cfg)

    def test_fallback_honors_core_discovery_rule(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The Gateway must resolve exactly like the CLI: ``NAHIDA_CONFIG``
        env var > ``./config.yaml`` — not a private ``CONFIG_YAML`` var."""
        cfg = tmp_path / "custom-run.yaml"
        cfg.write_text("app_name: Custom\n", encoding="utf-8")
        monkeypatch.setenv("NAHIDA_CONFIG", str(cfg))
        monkeypatch.setenv("CONFIG_YAML", str(tmp_path / "stale-legacy.yaml"))

        content = config_service.read_current_config()
        assert content.path == str(cfg)
        assert content.raw == "app_name: Custom\n"
