"""Configuration models for the image generation plugin."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OpenAIImagesBackendConfig(BaseModel):
    """Configuration for one OpenAI-compatible Images API backend."""

    model_config = ConfigDict(frozen=True, extra="allow")

    type: Literal["openai-images"] = "openai-images"
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
    trust_env: bool = False
    force_close_connections: bool = True
    max_concurrency: int = Field(default=1, ge=1, le=16)
    max_images_per_request: int = Field(default=1, ge=1, le=10)
    extra_body: dict[str, Any] = Field(default_factory=dict)


class MiniMaxBackendConfig(BaseModel):
    """Configuration for one MiniMax Image Generation API backend."""

    model_config = ConfigDict(frozen=True, extra="allow")

    type: Literal["minimax"] = "minimax"
    base_url: str = "https://api.minimaxi.com"
    api_key: str = ""
    require_api_key: bool = True
    model: str = "image-01"
    aspect_ratio: str = "1:1"
    width: int = 0
    height: int = 0
    style_type: str = ""
    style_weight: float = Field(default=0.8, ge=0.01, le=1.0)
    response_format: str = "url"
    seed: int | None = None
    prompt_optimizer: bool = False
    aigc_watermark: bool = False
    timeout_seconds: float = Field(default=120.0, ge=0.1)
    download_timeout_seconds: float = Field(default=60.0, ge=0.1)
    trust_env: bool = False
    force_close_connections: bool = True
    max_concurrency: int = Field(default=1, ge=1, le=16)
    max_images_per_request: int = Field(default=1, ge=1, le=9)
    extra_body: dict[str, Any] = Field(default_factory=dict)


BackendConfig = Annotated[
    Union[OpenAIImagesBackendConfig, MiniMaxBackendConfig],
    Field(discriminator="type"),
]


class ImageGenerationConfig(BaseModel):
    """Runtime configuration for image generation command/tool wrappers."""

    model_config = ConfigDict(frozen=True, extra="allow")

    provider: str = "default"
    backends: dict[str, BackendConfig] = Field(
        default_factory=lambda: {"default": OpenAIImagesBackendConfig()}
    )
    output_dir: str = "generated/images"
    auto_send: bool = True
    command_names: list[str] = Field(default_factory=lambda: ["draw", "生图"])
    caption_template: str = ""
    max_images_per_24h: int = Field(default=0, ge=0)

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
            "trust_env",
            "force_close_connections",
            "max_concurrency",
            "max_images_per_request",
            "extra_body",
        }
        backend = {key: data[key] for key in legacy_keys if key in data}
        if not backend:
            return data
        backend.setdefault("type", "openai-images")
        migrated = dict(data)
        for key in legacy_keys:
            migrated.pop(key, None)
        migrated.setdefault("provider", "default")
        migrated["backends"] = {"default": backend}
        return migrated

    def backend(
        self, name: str = ""
    ) -> OpenAIImagesBackendConfig | MiniMaxBackendConfig:
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
