"""Tests for ModelCapabilities dataclass."""

from dataclasses import FrozenInstanceError

import pytest

from nahida_bot.agent.providers.base import ModelCapabilities


class TestModelCapabilitiesDefaults:
    def test_defaults(self) -> None:
        cap = ModelCapabilities()
        assert cap.text_input is True
        assert cap.image_input is False
        assert cap.tool_calling is True
        assert cap.reasoning is False
        assert cap.prompt_cache is False
        assert cap.prompt_cache_images is False
        assert cap.explicit_context_cache is False
        assert cap.prompt_cache_min_tokens == 0
        assert cap.max_image_count == 0
        assert cap.max_image_bytes == 0
        assert cap.supported_image_mime_types == (
            "image/jpeg",
            "image/png",
            "image/webp",
        )
        assert cap.context_window is None
        assert cap.max_context_window is None
        assert cap.auto_compact_token_limit is None
        assert cap.effective_context_window_percent == 95

    def test_frozen(self) -> None:
        cap = ModelCapabilities()
        with pytest.raises(FrozenInstanceError):
            cap.image_input = True  # type: ignore[misc]

    def test_custom_values(self) -> None:
        cap = ModelCapabilities(
            image_input=True,
            prompt_cache=True,
            context_window=128000,
            max_image_count=5,
            max_image_bytes=20 * 1024 * 1024,
        )
        assert cap.image_input is True
        assert cap.prompt_cache is True
        assert cap.context_window == 128000
        assert cap.max_image_count == 5
        assert cap.max_image_bytes == 20 * 1024 * 1024

    def test_context_window_helpers(self) -> None:
        cap = ModelCapabilities(max_context_window=1_000_000)

        assert cap.resolved_context_window() == 1_000_000
        assert cap.resolved_auto_compact_token_limit() == 900_000

    def test_context_window_prefers_explicit_context_window(self) -> None:
        cap = ModelCapabilities(
            context_window=272000,
            max_context_window=1_000_000,
            auto_compact_token_limit=200000,
        )

        assert cap.resolved_context_window() == 272000
        assert cap.resolved_auto_compact_token_limit() == 200000

    def test_effective_context_window_percent_falls_back_to_95(self) -> None:
        cap = ModelCapabilities(effective_context_window_percent=0)

        assert cap.normalized_effective_context_window_percent() == 95

    def test_slots(self) -> None:
        cap = ModelCapabilities()
        assert not hasattr(cap, "__dict__")
