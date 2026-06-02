"""Plugin system — discovery, lifecycle management, and runtime isolation."""

from nahida_bot.plugins.base import (  # noqa: F401
    BotAPI,
    ChannelService,
    InboundMessage,
    MemoryRef,
    OutboundMessage,
    Plugin,
    PluginLogger,
    SessionInfo,
    SubscriptionHandle,
)
from nahida_bot.plugins.commands import (
    CommandEntry,
    CommandHandlerResult,
    CommandInfo,
    CommandMatch,
    CommandMatcher,
    CommandRegistry,
    CommandResult,
)
from nahida_bot.plugins.loader import PluginLoader
from nahida_bot.plugins.manager import PluginManager, PluginState
from nahida_bot.plugins.manifest import (
    Capabilities,
    FilesystemPermission,
    ManifestParseError,
    MemoryPermission,
    NetworkPermission,
    Permissions,
    PluginDependency,
    PluginManifest,
    SystemPermission,
    parse_manifest,
)
from nahida_bot.plugins.permissions import PermissionChecker
from nahida_bot.plugins.registry import HandlerRegistry, ToolRegistry
from nahida_bot.plugins.tool_executor import RegistryToolExecutor

__all__ = [
    # Base
    "BotAPI",
    "InboundMessage",
    "MemoryRef",
    "OutboundMessage",
    "Plugin",
    "PluginLogger",
    "SessionInfo",
    "SubscriptionHandle",
    # Channel
    "ChannelService",
    # Commands
    "CommandEntry",
    "CommandHandlerResult",
    "CommandInfo",
    "CommandMatch",
    "CommandMatcher",
    "CommandRegistry",
    "CommandResult",
    # Loader
    "PluginLoader",
    # Manager
    "PluginManager",
    "PluginState",
    # Manifest
    "Capabilities",
    "FilesystemPermission",
    "ManifestParseError",
    "MemoryPermission",
    "NetworkPermission",
    "Permissions",
    "PluginDependency",
    "PluginManifest",
    "SystemPermission",
    "parse_manifest",
    # Permissions
    "PermissionChecker",
    # Registry
    "HandlerRegistry",
    "RegistryToolExecutor",
    "ToolRegistry",
]
