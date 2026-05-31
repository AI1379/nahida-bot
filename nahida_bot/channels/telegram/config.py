"""Configuration model for the Telegram channel plugin."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

GroupTriggerMode = Literal["mention", "command", "always"]


class TelegramPluginConfig(BaseModel):
    """Runtime configuration for the Telegram Bot channel."""

    model_config = ConfigDict(extra="allow")

    bot_token: str = Field(
        default="",
        description=(
            "Telegram bot token. Empty allows TELEGRAM_BOT_TOKEN to provide it."
        ),
    )
    proxy: str = Field(
        default="",
        description="Optional HTTP/SOCKS proxy URL. Empty disables proxying.",
    )

    polling_timeout: int = Field(default=30, ge=1)
    polling_max_backoff: float = Field(default=30.0, gt=0)
    allowed_chats: list[str] = Field(
        default_factory=list,
        description="Optional chat allow-list. Empty means all chats.",
    )

    group_trigger_mode: GroupTriggerMode = Field(
        default="always",
        description="How group messages trigger the bot: mention, command, or always.",
    )
    group_context_capture: bool = Field(
        default=False,
        description=(
            "When true, non-triggering group messages are published as observed "
            "context instead of being dropped."
        ),
    )
    reply_to_inbound: bool | None = Field(
        default=None,
        description=(
            "Optional override for the router's default reply-to-inbound behavior. "
            "Null means use the global router setting."
        ),
    )

    send_retry_attempts: int = Field(default=3, ge=1)
    media_download_dir: str = Field(default="./data/temp/media")

    @field_validator("bot_token", "proxy", "media_download_dir")
    @classmethod
    def _strip_strings(cls, value: str) -> str:
        return value.strip()

    @field_validator("allowed_chats", mode="before")
    @classmethod
    def _coerce_chat_list(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (str, int)):
            return [str(value)]
        if isinstance(value, list):
            return [str(item) for item in value if str(item)]
        raise TypeError("allowed_chats must be a string, integer, or list")


def parse_telegram_config(raw: dict[str, Any] | None) -> TelegramPluginConfig:
    """Parse a plugin manifest config mapping into ``TelegramPluginConfig``."""
    return TelegramPluginConfig.model_validate(raw or {})
