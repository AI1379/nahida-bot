"""Tests for provider registry (Phase 2.8)."""

from __future__ import annotations

import pytest

from nahida_bot.agent.providers.registry import (
    ProviderDescriptor,
    get_provider_class,
    list_providers,
    register_provider,
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
