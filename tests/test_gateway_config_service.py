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


class TestPatchPreservesComments:
    def test_patch_round_trip_keeps_comments(self, tmp_path: Path) -> None:
        """WebUI patch saves must not strip comments from hand-written configs."""
        config_path = tmp_path / "config-run.yaml"
        config_path.write_text(
            """# top comment
app_name: Demo Bot   # inline comment
debug: false

providers:
  # my provider notes
  default:
    type: openai-compatible
    api_key: secret-key
    base_url: https://old.example
    models:
      - demo-model
""",
            encoding="utf-8",
        )
        checksum = config_service.read_current_config(
            config_path=str(config_path)
        ).checksum

        result = config_service.save_config_patch_with_backup(
            changes=[
                {"path": "debug", "value": True},
                {"path": "providers.default.base_url", "value": "https://new.example"},
            ],
            expected_checksum=checksum,
            config_path=str(config_path),
            backup_dir=str(tmp_path / "config_backups"),
        )
        assert result.saved, result.validation

        text = config_path.read_text(encoding="utf-8")
        assert "# top comment" in text
        assert "# inline comment" in text
        assert "# my provider notes" in text
        assert "debug: true" in text
        assert "https://new.example" in text
        assert "secret-key" in text
        assert "***" not in text
