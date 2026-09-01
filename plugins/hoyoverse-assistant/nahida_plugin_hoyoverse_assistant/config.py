"""Configuration for the HoYoverse assistant plugin."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Language = Literal[
    "zh-cn",
    "zh-tw",
    "de-de",
    "en-us",
    "es-es",
    "fr-fr",
    "id-id",
    "it-it",
    "ja-jp",
    "ko-kr",
    "pt-pt",
    "ru-ru",
    "th-th",
    "vi-vn",
    "tr-tr",
]


class HoyoverseAssistantConfig(BaseModel):
    """Validated non-secret runtime configuration."""

    # Ignore legacy static ``cookies`` config so upgrades cannot accidentally
    # retain it in model repr/logging. Credentials are user-scoped only.
    model_config = ConfigDict(extra="ignore")

    region: Literal["cn", "os"] = "cn"
    language: Language = "zh-cn"
    proxy: str = ""
    request_timeout_seconds: float = Field(default=20.0, ge=3.0, le=120.0)
    max_concurrency: int = Field(default=2, ge=1, le=10)
    qr_login_ttl_seconds: int = Field(default=180, ge=30, le=600)
    include_real_time_notes: bool = True

    @field_validator("language", "proxy")
    @classmethod
    def _strip_strings(cls, value: str) -> str:
        return value.strip()
