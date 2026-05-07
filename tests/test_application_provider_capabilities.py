"""Tests for provider capability parsing used during application startup."""

from nahida_bot.core.app import (
    _model_capabilities_from_config,
    _provider_model_entries,
)
from nahida_bot.core.config import ProviderModelConfig


def test_model_capabilities_from_config_parses_known_fields() -> None:
    cap = _model_capabilities_from_config(
        {
            "image_input": True,
            "prompt_cache": True,
            "supported_image_mime_types": ["image/png"],
            "image_generation": True,
            "web_search": True,
            "unknown": "ignored",
        }
    )

    assert cap.image_input is True
    assert cap.prompt_cache is True
    assert cap.image_generation is True
    assert cap.web_search is True
    assert cap.supported_image_mime_types == ("image/png",)


def test_model_capabilities_from_empty_config_uses_defaults() -> None:
    cap = _model_capabilities_from_config({})

    assert cap.image_input is False
    assert cap.tool_calling is True


def test_provider_model_entries_normalizes_strings_and_objects() -> None:
    entries = _provider_model_entries(
        [
            "text-model",
            ProviderModelConfig(
                name="vision-model",
                capabilities={"image_input": True},
            ),
        ]
    )

    assert entries == [
        ("text-model", {}),
        ("vision-model", {"image_input": True}),
    ]
