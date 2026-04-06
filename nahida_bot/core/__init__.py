"""Core application infrastructure."""

from nahida_bot.core.exceptions import (
    NahidaBotError,
    ConfigError,
    ApplicationError,
    PluginError,
)

__all__ = [
    "NahidaBotError",
    "ConfigError",
    "ApplicationError",
    "PluginError",
]
