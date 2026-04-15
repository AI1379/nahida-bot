"""Runtime permission checker for plugin API calls."""

from __future__ import annotations

import fnmatch

from nahida_bot.core.exceptions import PermissionDenied
from nahida_bot.plugins.manifest import PluginManifest


class PermissionChecker:
    """Validates plugin API calls against manifest-declared permissions.

    Every RealBotAPI method consults this checker before delegating to
    the real implementation. Violations raise ``PermissionDenied``.
    """

    def __init__(self, manifest: PluginManifest) -> None:
        self._manifest = manifest

    def check_network_outbound(self, url: str) -> None:
        """Check if the plugin may access an outbound URL."""
        patterns = self._manifest.permissions.network.outbound
        if not patterns:
            raise PermissionDenied(
                f"Plugin '{self._manifest.id}' has no outbound network permissions"
            )
        if not self._match_patterns(url, patterns):
            raise PermissionDenied(
                f"Plugin '{self._manifest.id}' cannot access {url} "
                f"(no matching outbound network permission)"
            )

    def check_network_inbound(self) -> None:
        """Check if the plugin may receive inbound network requests."""
        if not self._manifest.permissions.network.inbound:
            raise PermissionDenied(
                f"Plugin '{self._manifest.id}' has no inbound network permission"
            )

    def check_filesystem_read(self, zone: str) -> None:
        """Check if the plugin may read from the given filesystem zone."""
        allowed = self._manifest.permissions.filesystem.read
        if zone not in allowed:
            raise PermissionDenied(
                f"Plugin '{self._manifest.id}' cannot read from zone '{zone}' "
                f"(allowed: {', '.join(allowed) or 'none'})"
            )

    def check_filesystem_write(self, zone: str) -> None:
        """Check if the plugin may write to the given filesystem zone."""
        allowed = self._manifest.permissions.filesystem.write
        if zone not in allowed:
            raise PermissionDenied(
                f"Plugin '{self._manifest.id}' cannot write to zone '{zone}' "
                f"(allowed: {', '.join(allowed) or 'none'})"
            )

    def check_memory_read(self) -> None:
        """Check if the plugin may read from the memory store."""
        if not self._manifest.permissions.memory.read:
            raise PermissionDenied(
                f"Plugin '{self._manifest.id}' has no memory read permission"
            )

    def check_memory_write(self) -> None:
        """Check if the plugin may write to the memory store."""
        if not self._manifest.permissions.memory.write:
            raise PermissionDenied(
                f"Plugin '{self._manifest.id}' has no memory write permission"
            )

    def check_subprocess(self) -> None:
        """Check if the plugin may execute subprocesses."""
        if not self._manifest.permissions.system.subprocess:
            raise PermissionDenied(
                f"Plugin '{self._manifest.id}' has no subprocess execution permission"
            )

    def check_env_var(self, key: str) -> None:
        """Check if the plugin may read the given environment variable."""
        prefixes = self._manifest.permissions.system.env_vars
        if not prefixes:
            raise PermissionDenied(
                f"Plugin '{self._manifest.id}' has no environment variable "
                f"read permission"
            )
        if not any(fnmatch.fnmatch(key, prefix) for prefix in prefixes):
            raise PermissionDenied(
                f"Plugin '{self._manifest.id}' cannot read env var '{key}' "
                f"(allowed prefixes: {', '.join(prefixes)})"
            )

    @staticmethod
    def _match_patterns(value: str, patterns: list[str]) -> bool:
        """Glob-style match of value against a list of patterns."""
        return any(fnmatch.fnmatch(value, p) for p in patterns)
