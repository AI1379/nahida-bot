"""Configuration models for the image generation plugin."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OpenAIImagesBackendConfig(BaseModel):
    """Configuration for one OpenAI-compatible Images API backend."""

    model_config = ConfigDict(frozen=True, extra="allow")

    type: str = "openai-images"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    require_api_key: bool = True
    model: str = "gpt-image-1"
    size: str = "1024x1024"
    quality: str = "auto"
    response_format: str = ""
    output_format: str = ""
    timeout_seconds: float = Field(default=120.0, ge=0.1)
    download_timeout_seconds: float = Field(default=60.0, ge=0.1)
    max_concurrency: int = Field(default=1, ge=1, le=16)
    max_images_per_request: int = Field(default=1, ge=1, le=10)
    extra_body: dict[str, Any] = Field(default_factory=dict)


class ImageGenerationConfig(BaseModel):
    """Runtime configuration for image generation command/tool wrappers."""

    model_config = ConfigDict(frozen=True, extra="allow")

    enabled: bool = False
    provider: str = "default"
    backends: dict[str, OpenAIImagesBackendConfig] = Field(
        default_factory=lambda: {"default": OpenAIImagesBackendConfig()}
    )
    output_dir: str = "generated/images"
    auto_send: bool = True
    command_names: list[str] = Field(default_factory=lambda: ["draw", "生图"])
    caption_template: str = ""

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_direct_config(cls, data: Any) -> Any:
        """Accept the first direct-config version by wrapping it as a backend."""

        if not isinstance(data, dict) or "backends" in data:
            return data
        legacy_keys = {
            "base_url",
            "api_key",
            "require_api_key",
            "model",
            "size",
            "quality",
            "response_format",
            "output_format",
            "timeout_seconds",
            "download_timeout_seconds",
            "max_concurrency",
            "max_images_per_request",
            "extra_body",
        }
        backend = {key: data[key] for key in legacy_keys if key in data}
        if not backend:
            return data
        migrated = dict(data)
        for key in legacy_keys:
            migrated.pop(key, None)
        migrated.setdefault("provider", "default")
        migrated["backends"] = {"default": backend}
        return migrated

    def backend(self, name: str = "") -> OpenAIImagesBackendConfig:
        """Return the selected backend config, raising for unknown providers."""

        provider = name.strip() or self.provider
        try:
            return self.backends[provider]
        except KeyError as exc:
            available = ", ".join(sorted(self.backends)) or "(none)"
            raise ValueError(
                f"Image generation provider '{provider}' is not configured. "
                f"Available providers: {available}"
            ) from exc


def parse_image_generation_config(raw: dict[str, Any]) -> ImageGenerationConfig:
    """Parse raw manifest config into a typed config object."""

    return ImageGenerationConfig(**raw)
