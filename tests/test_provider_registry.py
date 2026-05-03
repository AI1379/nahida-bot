"""Tests for provider registry (Phase 2.8)."""

from __future__ import annotations

import pytest

from nahida_bot.agent.providers.registry import (
    ProviderDescriptor,
    clear_runtime_providers,
    create_provider,
    get_provider_class,
    list_providers,
    register_provider,
    register_runtime_provider,
    unregister_runtime_provider,
)


class TestProviderRegistry:
    """Tests for the @register_provider decorator and lookup functions."""

    def test_builtin_providers_registered(self) -> None:
        """All built-in providers should be auto-registered on import."""
        # Importing the providers package triggers registration
        import nahida_bot.agent.providers  # noqa: F401

        registered_types = {d.provider_type for d in list_providers()}
        assert "openai-compatible" in registered_types
        assert "deepseek" in registered_types
        assert "glm" in registered_types
        assert "groq" in registered_types
        assert "minimax" in registered_types

    def test_get_provider_class_returns_correct_class(self) -> None:
        from nahida_bot.agent.providers.openai_compatible import (
            OpenAICompatibleProvider,
        )

        cls = get_provider_class("openai-compatible")
        assert cls is OpenAICompatibleProvider

    def test_get_provider_class_returns_none_for_unknown(self) -> None:
        cls = get_provider_class("nonexistent-provider")
        assert cls is None

    def test_list_providers_returns_descriptors(self) -> None:
        providers = list_providers()
        assert len(providers) >= 5
        assert all(isinstance(d, ProviderDescriptor) for d in providers)

    def test_duplicate_registration_raises(self) -> None:
        from nahida_bot.agent.providers.base import ChatProvider

        # The type "deepseek" is already registered
        with pytest.raises(ValueError, match="already registered"):

            @register_provider("deepseek", "Duplicate")
            class DuplicateProvider(ChatProvider):  # type: ignore[type-arg]
                name = "dup"
                api_family = "test"

                @property
                def tokenizer(self):  # type: ignore[override]
                    return None

                async def chat(self, **kwargs):  # type: ignore[override]
                    ...

    def test_deepseek_provider_subclass(self) -> None:
        from nahida_bot.agent.providers.deepseek import DeepSeekProvider

        cls = get_provider_class("deepseek")
        assert cls is DeepSeekProvider

    def test_groq_provider_subclass(self) -> None:
        from nahida_bot.agent.providers.groq import GroqProvider

        cls = get_provider_class("groq")
        assert cls is GroqProvider

    def test_runtime_provider_registration_and_unregister(self) -> None:
        from nahida_bot.agent.providers.base import ChatProvider

        class RuntimeProvider(ChatProvider):  # type: ignore[type-arg]
            name = "runtime"
            api_family = "test"

            def __init__(self, config: dict[str, object]) -> None:
                self.config = config

            @property
            def tokenizer(self):  # type: ignore[override]
                return None

            async def chat(self, **kwargs):  # type: ignore[override]
                ...

        provider_type = "runtime-test-provider"
        unregister_runtime_provider(provider_type)
        try:
            register_runtime_provider(
                provider_type,
                lambda config: RuntimeProvider(config),
                owner_plugin_id="provider-plugin",
            )

            provider = create_provider(provider_type, model="local-model")

            assert isinstance(provider, RuntimeProvider)
            assert provider.config["model"] == "local-model"
            assert unregister_runtime_provider(
                provider_type, owner_plugin_id="provider-plugin"
            )
        finally:
            unregister_runtime_provider(provider_type)

    def test_runtime_provider_cannot_shadow_builtin(self) -> None:
        with pytest.raises(ValueError, match="already registered"):
            register_runtime_provider("deepseek", lambda config: None)  # type: ignore[arg-type,return-value]

    def test_clear_runtime_providers_can_filter_by_owner(self) -> None:
        clear_runtime_providers()
        try:
            register_runtime_provider(
                "owned-runtime-provider",
                lambda config: None,  # type: ignore[return-value]
                owner_plugin_id="owner-a",
            )
            register_runtime_provider(
                "other-runtime-provider",
                lambda config: None,  # type: ignore[return-value]
                owner_plugin_id="owner-b",
            )

            assert clear_runtime_providers(owner_plugin_id="owner-a") == 1

            provider_types = {d.provider_type for d in list_providers()}
            assert "owned-runtime-provider" not in provider_types
            assert "other-runtime-provider" in provider_types
        finally:
            clear_runtime_providers()
