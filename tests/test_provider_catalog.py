"""Tests for the provider catalog (shared provider templates)."""

from __future__ import annotations

from nahida_bot.agent.providers.catalog import (
    PROVIDER_PRESETS,
    ProviderTemplate,
    is_known_provider_type,
    preset_for_type,
    preset_with_base_url,
)


class TestPresets:
    def test_every_registered_builtin_type_has_a_preset(self) -> None:
        preset_types = {preset.provider_type for preset in PROVIDER_PRESETS.values()}
        for known in (
            "anthropic",
            "codex",
            "deepseek",
            "glm",
            "groq",
            "minimax",
            "openai-compatible",
            "openai-responses",
        ):
            assert known in preset_types

    def test_render_entry_includes_key_placeholder(self) -> None:
        entry = PROVIDER_PRESETS["deepseek"].render_entry()
        assert entry["type"] == "deepseek"
        assert entry["api_key"] == "${DEEPSEEK_LLM_API_KEY:}"
        assert {"name": "deepseek-chat", "tags": ["primary"]} in entry["models"]

    def test_render_entry_omits_api_key_for_oauth_only_types(self) -> None:
        entry = PROVIDER_PRESETS["codex"].render_entry()
        assert entry["type"] == "codex"
        assert "api_key" not in entry
        assert entry["stream_responses"] is True

    def test_render_entry_is_a_fresh_copy(self) -> None:
        preset = PROVIDER_PRESETS["deepseek"]
        first = preset.render_entry()
        first["models"].clear()
        second = preset.render_entry()
        assert second["models"], "render must not leak mutable preset state"


class TestLookup:
    def test_preset_for_type_matches_by_type(self) -> None:
        assert preset_for_type("codex") is PROVIDER_PRESETS["codex"]
        assert preset_for_type("openai-compatible") is PROVIDER_PRESETS["siliconflow"]
        assert preset_for_type("nope") is None

    def test_is_known_provider_type(self) -> None:
        assert is_known_provider_type("codex")
        assert is_known_provider_type("minimax")
        assert not is_known_provider_type("bogus-relay")

    def test_preset_with_base_url_returns_modified_copy(self) -> None:
        base = PROVIDER_PRESETS["generic-openai"]
        changed = preset_with_base_url(base, "https://relay.example/v1")
        assert changed is not base
        assert changed.base_url == "https://relay.example/v1"
        assert base.base_url == ""


class TestProviderTemplate:
    def test_generic_template_defaults_key_env(self) -> None:
        template = ProviderTemplate(label="x", provider_type="openai-compatible")
        assert template.render_entry()["api_key"] == "${LLM_API_KEY:}"
