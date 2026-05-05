"""Tests for MultimodalConfig and related config extensions."""

import pytest
from pydantic import ValidationError

from nahida_bot.core.config import (
    MultimodalConfig,
    ProviderEntryConfig,
    ProviderModelConfig,
    Settings,
)


class TestMultimodalConfig:
    def test_defaults(self) -> None:
        cfg = MultimodalConfig()
        assert cfg.image_fallback_mode == "auto"
        assert cfg.media_context_policy == "cache_aware"
        assert cfg.image_fallback_provider == ""
        assert cfg.image_fallback_model == ""
        assert cfg.max_images_per_turn == 4
        assert cfg.max_image_bytes == 10485760
        assert cfg.media_cache_ttl_seconds == 3600

    def test_custom_values(self) -> None:
        cfg = MultimodalConfig(
            image_fallback_mode="tool",
            media_context_policy="native_recent",
            image_fallback_provider="vision",
            image_fallback_model="gpt-5.2",
            max_images_per_turn=8,
            max_image_bytes=20 * 1024 * 1024,
            media_cache_ttl_seconds=600,
        )
        assert cfg.image_fallback_mode == "tool"
        assert cfg.media_context_policy == "native_recent"
        assert cfg.image_fallback_provider == "vision"
        assert cfg.image_fallback_model == "gpt-5.2"
        assert cfg.max_images_per_turn == 8
        assert cfg.max_image_bytes == 20 * 1024 * 1024
        assert cfg.media_cache_ttl_seconds == 600

    @pytest.mark.parametrize("mode", ["bad", "", "AUTO"])
    def test_invalid_fallback_mode_rejected(self, mode: str) -> None:
        with pytest.raises(ValidationError):
            MultimodalConfig.model_validate({"image_fallback_mode": mode})

    @pytest.mark.parametrize("policy", ["bad", "", "cache-aware"])
    def test_invalid_media_context_policy_rejected(self, policy: str) -> None:
        with pytest.raises(ValidationError):
            MultimodalConfig.model_validate({"media_context_policy": policy})

    def test_negative_limits_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MultimodalConfig(max_images_per_turn=-1)
        with pytest.raises(ValidationError):
            MultimodalConfig(max_image_bytes=-1)
        with pytest.raises(ValidationError):
            MultimodalConfig(media_cache_ttl_seconds=-1)


class TestSettingsMultimodal:
    def test_default_multimodal(self) -> None:
        s = Settings()
        assert isinstance(s.multimodal, MultimodalConfig)
        assert s.multimodal.image_fallback_mode == "auto"

    def test_multimodal_from_dict(self) -> None:
        s = Settings(
            multimodal=MultimodalConfig(
                image_fallback_mode="off",
                max_images_per_turn=2,
            )
        )
        assert s.multimodal.image_fallback_mode == "off"
        assert s.multimodal.max_images_per_turn == 2


class TestProviderEntryConfigModels:
    def test_default_models(self) -> None:
        cfg = ProviderEntryConfig()
        assert cfg.models == []

    def test_models_accept_strings_and_config_objects(self) -> None:
        cfg = ProviderEntryConfig(
            models=[
                "gpt-5-nano",
                ProviderModelConfig(
                    name="gpt-5.2",
                    capabilities={"image_input": True},
                ),
            ]
        )
        assert cfg.models[0] == "gpt-5-nano"
        assert isinstance(cfg.models[1], ProviderModelConfig)
        assert cfg.models[1].name == "gpt-5.2"
        assert cfg.models[1].capabilities["image_input"] is True

    def test_models_from_dict(self) -> None:
        cfg = ProviderEntryConfig.model_validate(
            {
                "models": [
                    "text-model",
                    {
                        "name": "vision-model",
                        "capabilities": {"image_input": True},
                    },
                ]
            }
        )
        assert cfg.models[0] == "text-model"
        assert isinstance(cfg.models[1], ProviderModelConfig)
        assert cfg.models[1].capabilities["image_input"] is True
