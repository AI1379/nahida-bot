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

    def test_frozen(self) -> None:
        cap = ModelCapabilities()
        with pytest.raises(FrozenInstanceError):
            cap.image_input = True  # type: ignore[misc]

    def test_custom_values(self) -> None:
        cap = ModelCapabilities(
            image_input=True,
            prompt_cache=True,
            max_image_count=5,
            max_image_bytes=20 * 1024 * 1024,
        )
        assert cap.image_input is True
        assert cap.prompt_cache is True
        assert cap.max_image_count == 5
        assert cap.max_image_bytes == 20 * 1024 * 1024

    def test_slots(self) -> None:
        cap = ModelCapabilities()
        assert not hasattr(cap, "__dict__")
