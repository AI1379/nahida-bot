"""OneBot channel plugin."""

from nahida_bot.channels.onebot.config import OneBotPluginConfig, parse_onebot_config
from nahida_bot.channels.onebot.plugin import OneBotPlugin

__all__ = [
    "OneBotPlugin",
    "OneBotPluginConfig",
    "parse_onebot_config",
]
