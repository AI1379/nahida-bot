"""Plugin system — discovery, lifecycle management, and runtime isolation."""

from nahida_bot.plugins.base import (
    BotAPI,
    InboundMessage,
    MemoryRef,
    OutboundMessage,
    Plugin,
    PluginLogger,
    SessionInfo,
    SubscriptionHandle,
)
from nahida_bot.plugins.loader import PluginLoader
from nahida_bot.plugins.manager import PluginManager, PluginState
from nahida_bot.plugins.manifest import (
    Capabilities,
    FilesystemPermission,
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
    # Loader
    "PluginLoader",
    # Manager
    "PluginManager",
    "PluginState",
    # Manifest
    "Capabilities",
    "FilesystemPermission",
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
    "ToolRegistry",
]
