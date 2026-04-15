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


class StartupError(ApplicationError):
    """Application startup/initialization error."""

    pass


class CommunicationError(ApplicationError):
    """Node, gateway, or channel communication error."""

    pass


class PluginError(NahidaBotError):
    """Plugin loading or execution error."""

    pass


class PermissionDenied(PluginError):
    """Raised when a plugin attempts an action beyond its declared permissions."""

    pass


class PluginLoadError(PluginError):
    """Raised when a plugin manifest is invalid or the module cannot be imported."""

    pass


class PluginStateError(PluginError):
    """Raised when a lifecycle transition is invalid for the plugin's current state."""

    pass
