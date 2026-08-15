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


class TestBackupRestore:
    BASE_CONFIG = """# my config
app_name: Demo Bot
debug: false

providers:
  default:
    type: openai-compatible
    api_key: secret-key
    base_url: https://old.example
    models:
      - demo-model
"""

    def _patch_debug(self, tmp_path: Path) -> object:
        checksum = config_service.read_current_config(
            config_path=str(tmp_path / "config-run.yaml")
        ).checksum
        return config_service.save_config_patch_with_backup(
            changes=[{"path": "debug", "value": True}],
            expected_checksum=checksum,
            config_path=str(tmp_path / "config-run.yaml"),
        )

    def test_restore_round_trip(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config-run.yaml"
        cfg.write_text(self.BASE_CONFIG, encoding="utf-8")
        original_checksum = config_service.read_current_config(
            config_path=str(cfg)
        ).checksum

        assert self._patch_debug(tmp_path).saved
        assert "debug: true" in cfg.read_text(encoding="utf-8")

        backups = config_service.list_backups(config_path=str(cfg))
        assert len(backups) == 1
        result = config_service.restore_config_backup(
            backups[0]["name"], config_path=str(cfg)
        )
        assert result.saved, result.validation
        assert cfg.read_text(encoding="utf-8") == self.BASE_CONFIG
        assert result.checksum == original_checksum
        # The overwritten state itself got backed up: restore is reversible.
        assert len(config_service.list_backups(config_path=str(cfg))) == 2

    def test_restore_rejects_stale_checksum(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config-run.yaml"
        cfg.write_text(self.BASE_CONFIG, encoding="utf-8")
        assert self._patch_debug(tmp_path).saved
        backups = config_service.list_backups(config_path=str(cfg))

        result = config_service.restore_config_backup(
            backups[0]["name"],
            config_path=str(cfg),
            expected_checksum="sha256:stale",
        )
        assert not result.saved
        assert "debug: true" in cfg.read_text(encoding="utf-8")

    def test_restore_rejects_unknown_backup(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config-run.yaml"
        cfg.write_text(self.BASE_CONFIG, encoding="utf-8")
        result = config_service.restore_config_backup(
            "../evil.bak", config_path=str(cfg)
        )
        assert not result.saved

    def test_restore_rejects_invalid_backup_content(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config-run.yaml"
        cfg.write_text(self.BASE_CONFIG, encoding="utf-8")
        assert self._patch_debug(tmp_path).saved
        bdir = tmp_path / "config_backups"
        (bdir / "config.yaml.corrupt.bak").write_text(
            "providers: [broken\n", encoding="utf-8"
        )
        result = config_service.restore_config_backup(
            "config.yaml.corrupt.bak", config_path=str(cfg)
        )
        assert not result.saved
        assert result.validation is not None and result.validation.errors > 0


class TestBackupPrune:
    def test_prune_keeps_newest(self, tmp_path: Path) -> None:
        bdir = tmp_path / "config_backups"
        bdir.mkdir()
        for i in range(40):
            (bdir / f"config.yaml.2026010{i:02d}-000000.bak").write_text(
                f"v{i}\n", encoding="utf-8"
            )
        config_service._prune_backups(bdir, keep=5)
        remaining = sorted(p.name for p in bdir.glob("*.bak"))
        assert len(remaining) == 5
        # Sorted ascending; the five newest (35..39) survive.
        assert remaining[0] == "config.yaml.202601035-000000.bak"
