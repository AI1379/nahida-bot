"""Tests for model-declared sensitive config field knowledge."""

from __future__ import annotations

from nahida_bot.core.config import Settings
from nahida_bot.core.config_secrets import (
    is_sensitive_path,
    path_matches_pattern,
    sensitive_path_patterns,
)


class TestPatternDerivation:
    def test_provider_api_key_patterns_derived(self) -> None:
        patterns = sensitive_path_patterns()
        assert "providers.*.api_key" in patterns
        assert "providers.*.quota.api_key" in patterns

    def test_gateway_and_webui_secrets_derived(self) -> None:
        patterns = sensitive_path_patterns()
        assert "webapi.auth_token" in patterns
        assert "webui.auth.admin_password_hash" in patterns

    def test_no_plain_fields_marked(self) -> None:
        patterns = sensitive_path_patterns()
        assert "app_name" not in patterns
        assert "providers.*.base_url" not in patterns


class TestPathMatching:
    def test_wildcard_matches_any_provider_id(self) -> None:
        assert path_matches_pattern(
            "providers.deepseek-main.api_key", "providers.*.api_key"
        )
        assert path_matches_pattern(
            "providers.deepseek-main.quota.api_key", "providers.*.quota.api_key"
        )

    def test_list_indices_are_ignored(self) -> None:
        assert (
            path_matches_pattern(
                "providers.p.models[0].api_key", "providers.p.models.*.api_key"
            )
            is False
        )  # different depth on purpose
        assert path_matches_pattern(
            "integrations[0].credential", "integrations.credential"
        )

    def test_exact_paths(self) -> None:
        assert path_matches_pattern("webapi.auth_token", "webapi.auth_token")
        assert not path_matches_pattern("webapi.auth_token", "webapi.host")


class TestIsSensitive:
    def test_model_declared_path(self) -> None:
        assert is_sensitive_path("webapi.auth_token", "auth_token")

    def test_regex_fallback_for_untyped_sections(self) -> None:
        # Channels/plugin sections are not part of the typed Settings tree.
        assert is_sensitive_path("telegram.bot_token", "bot_token")
        assert is_sensitive_path("integrations[0].api_key", "api_key")

    def test_non_sensitive(self) -> None:
        assert not is_sensitive_path("app_name", "app_name")
        assert not is_sensitive_path("providers.p.base_url", "base_url")


class TestSettingsStillParses:
    def test_sensitive_annotation_keeps_plain_str_type(self) -> None:
        settings = Settings(providers={"p": {"type": "deepseek", "api_key": "sk-x"}})
        assert settings.providers["p"].api_key == "sk-x"
        assert isinstance(settings.providers["p"].api_key, str)
