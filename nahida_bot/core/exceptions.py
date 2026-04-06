"""Core exception definitions."""


class NahidaBotError(Exception):
    """Base exception for Nahida Bot."""

    pass


class ConfigError(NahidaBotError):
    """Configuration error."""

    pass


class ApplicationError(NahidaBotError):
    """Application runtime error."""

    pass


class PluginError(NahidaBotError):
    """Plugin loading or execution error."""

    pass
