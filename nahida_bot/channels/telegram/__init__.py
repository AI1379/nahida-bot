"""Telegram channel plugin."""

from nahida_bot.channels.telegram.config import (
    TelegramPluginConfig,
    parse_telegram_config,
)
from nahida_bot.channels.telegram.plugin import TelegramPlugin

__all__ = ["TelegramPlugin", "TelegramPluginConfig", "parse_telegram_config"]
