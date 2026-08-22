"""Configuration model for the Discord channel plugin."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

GroupTriggerMode = Literal["none", "mention", "command", "always"]


class DiscordPluginConfig(BaseModel):
    """Runtime configuration for the Discord Bot channel."""

    model_config = ConfigDict(extra="allow")

    bot_token: str = Field(
        default="",
        description=(
            "Discord bot token. Empty allows DISCORD_BOT_TOKEN to provide it."
        ),
    )
    proxy: str = Field(
        default="",
        description="Optional HTTP/SOCKS proxy URL. Empty disables proxying.",
    )

    allowed_guilds: list[str] = Field(
        default_factory=list,
        description=(
            "Guild (server) allow-list. Empty means all guilds. "
            "DMs are governed by allowed_dm_users instead."
        ),
    )
    blocked_channels: list[str] = Field(
        default_factory=list,
        description=(
            "Channel/thread deny-list applied inside allowed guilds. "
            "Empty blocks nothing."
        ),
    )
    allowed_dm_users: list[str] = Field(
        default_factory=list,
        description="DM user allow-list. Empty means all DM partners.",
    )

    group_trigger_mode: GroupTriggerMode = Field(
        default="mention",
        description=(
            "How guild channel/thread messages trigger the bot: none, mention, "
            "command, or always. 'mention' requires @bot; 'command' means "
            "command prefix or @bot."
        ),
    )
    group_context_capture: bool = Field(
        default=False,
        description=(
            "When true, non-triggering guild messages are published as observed "
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

    message_max_length: int = Field(
        default=2000,
        ge=200,
        le=2000,
        description="Discord message character limit used for outbound splitting.",
    )
    register_slash_commands: bool = Field(
        default=True,
        description=(
            "Register native Discord slash commands for registered bot commands "
            "(guild-scoped, synced on gateway ready and on plugin changes)."
        ),
    )
    send_retry_attempts: int = Field(default=3, ge=1)
    media_download_dir: str = Field(default="./data/temp/media")

    @field_validator("bot_token", "proxy", "media_download_dir")
    @classmethod
    def _strip_strings(cls, value: str) -> str:
        return value.strip()

    @field_validator(
        "allowed_guilds", "blocked_channels", "allowed_dm_users", mode="before"
    )
    @classmethod
    def _coerce_id_list(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (str, int)):
            return [str(value)]
        if isinstance(value, list):
            return [str(item) for item in value if str(item)]
        raise ValueError("allow/deny id lists must be a string, integer, or list")


def parse_discord_config(raw: dict[str, Any] | None) -> DiscordPluginConfig:
    """Parse a plugin manifest config mapping into ``DiscordPluginConfig``."""
    return DiscordPluginConfig.model_validate(raw or {})
