"""Discord channel plugin package."""

from nahida_bot.channels.discord.config import (
    DiscordPluginConfig,
    parse_discord_config,
)
from nahida_bot.channels.discord.plugin import DiscordPlugin

__all__ = ["DiscordPlugin", "DiscordPluginConfig", "parse_discord_config"]
