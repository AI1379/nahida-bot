"""Tests for config discovery helpers, auto-loading, and pre-flight checks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nahida_bot.core.config import (
    ProviderEntryConfig,
    Settings,
    find_config_yaml,
    find_env_path,
    load_settings,
    load_settings_auto,
)
from nahida_bot.core.preflight import check_readiness


# ---------------------------------------------------------------------------
# find_config_yaml / find_env_path
# ---------------------------------------------------------------------------


class TestFinders:
    def test_explicit_config_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NAHIDA_CONFIG", "/from/env.yaml")
        assert find_config_yaml("/explicit.yaml") == "/explicit.yaml"

    def test_env_var_used_when_no_explicit(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("NAHIDA_CONFIG", "/from/env.yaml")
        # no ./config.yaml in tmp_path so env var must win
        assert find_config_yaml(None) == "/from/env.yaml"

    def test_cwd_config_used_when_no_explicit_or_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("NAHIDA_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.yaml").write_text("app_name: x", encoding="utf-8")
        assert find_config_yaml(None) == "config.yaml"

    def test_returns_none_when_nothing_found(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("NAHIDA_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)
        assert find_config_yaml(None) is None

    def test_env_path_explicit_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENV_PATH", "/from/env")
        assert find_env_path("/explicit.env") == "/explicit.env"

    def test_env_path_cwd_fallback(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("ENV_PATH", raising=False)
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("X=1", encoding="utf-8")
        assert find_env_path(None) == ".env"


# ---------------------------------------------------------------------------
# load_settings_auto
# ---------------------------------------------------------------------------


class TestLoadSettingsAuto:
    def test_discovers_cwd_config_and_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.delenv("NAHIDA_CONFIG", raising=False)
        monkeypatch.delenv("ENV_PATH", raising=False)
        monkeypatch.chdir(tmp_path)

        (tmp_path / ".env").write_text(
            'DEEPSEEK_LLM_API_KEY="sk-auto"\n', encoding="utf-8"
        )
        (tmp_path / "config.yaml").write_text(
            "providers:\n"
            "  ds:\n"
            "    type: deepseek\n"
            "    api_key: '${DEEPSEEK_LLM_API_KEY:}'\n"
            "    models:\n"
            "      - name: deepseek-chat\n"
            "        tags: [primary]\n"
            "default_provider: ds\n",
            encoding="utf-8",
        )

        settings = load_settings_auto()
        assert settings.providers["ds"].api_key == "sk-auto"
        assert settings.default_provider == "ds"

    def test_auto_is_hermetic_without_files(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.delenv("NAHIDA_CONFIG", raising=False)
        monkeypatch.delenv("ENV_PATH", raising=False)
        monkeypatch.chdir(tmp_path)
        settings = load_settings_auto()
        assert settings.providers == {}
        assert settings.app_name == "Nahida Bot"


# ---------------------------------------------------------------------------
# load_settings purity regression (must NOT auto-discover)
# ---------------------------------------------------------------------------


class TestLoadSettingsPurity:
    def test_load_settings_does_not_read_cwd_config(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """load_settings() with no args stays hermetic even if config.yaml exists."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.yaml").write_text(
            "log_file: ./should-not-be-loaded.log\n", encoding="utf-8"
        )
        settings = load_settings()
        assert settings.log_file is None  # built-in default, not from file


# ---------------------------------------------------------------------------
# preflight / check_readiness
# ---------------------------------------------------------------------------


def _settings_with(providers: dict[str, Any]) -> Settings:
    return Settings(
        providers={k: ProviderEntryConfig(**v) for k, v in providers.items()}
    )


class TestCheckReadiness:
    def test_no_providers_is_warning(self) -> None:
        report = check_readiness(Settings())
        assert report.errors == 0
        assert report.warnings == 1
        assert "no_providers" == report.issues[0].code

    def test_usable_provider_ok(self) -> None:
        s = _settings_with(
            {"ds": {"type": "deepseek", "api_key": "sk", "models": [{"name": "m"}]}}
        )
        report = check_readiness(s)
        assert report.ok
        assert report.issues == []

    def test_missing_api_key_is_warning(self) -> None:
        s = _settings_with({"ds": {"type": "deepseek", "models": [{"name": "m"}]}})
        report = check_readiness(s)
        assert report.errors == 0
        assert report.warnings == 1
        assert report.issues[0].code == "no_usable_provider"

    def test_stored_provider_credential_is_usable(self) -> None:
        s = _settings_with({"ds": {"type": "deepseek", "models": [{"name": "m"}]}})
        report = check_readiness(
            s,
            authenticated_provider_ids=frozenset({"ds"}),
        )
        assert report.ok
        assert report.issues == []

    def test_codex_provider_needs_no_key(self) -> None:
        s = _settings_with({"codex": {"type": "codex", "models": [{"name": "gpt"}]}})
        report = check_readiness(s)
        assert report.ok

    def test_unusable_default_provider_is_error(self) -> None:
        s = _settings_with(
            {
                "ds": {"type": "deepseek", "api_key": "sk", "models": [{"name": "m"}]},
                "dead": {"type": "deepseek", "models": [{"name": "m"}]},
            }
        )
        s = s.model_copy(update={"default_provider": "dead"})
        report = check_readiness(s)
        assert report.errors == 1
        codes = [i.code for i in report.issues]
        assert "default_provider_unusable" in codes

    def test_partial_skipped_warns(self) -> None:
        s = _settings_with(
            {
                "ds": {"type": "deepseek", "api_key": "sk", "models": [{"name": "m"}]},
                "extra": {"type": "deepseek", "models": [{"name": "m"}]},
            }
        )
        report = check_readiness(s)
        assert report.errors == 0
        assert report.warnings == 1
        assert report.issues[0].code == "providers_skipped"
