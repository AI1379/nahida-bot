"""Plugin discovery and dynamic loading."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from nahida_bot.core.exceptions import PluginLoadError
from nahida_bot.plugins.manifest import PluginManifest, parse_manifest

if TYPE_CHECKING:
    from nahida_bot.plugins.base import Plugin

logger = structlog.get_logger(__name__)


class PluginLoader:
    """Discovers plugins on disk and dynamically loads their entry classes."""

    def __init__(self) -> None:
        # Track which module path each plugin has bound to, so we can
        # enforce one-module-one-plugin and prevent unload conflicts.
        self._module_to_plugin: dict[str, str] = {}

    def discover(self, paths: list[Path]) -> list[tuple[PluginManifest, Path]]:
        """Scan directories for plugin.yaml files.

        Args:
            paths: List of directories to scan.

        Returns:
            List of (manifest, plugin_dir) tuples for each discovered plugin.
        """
        results: list[tuple[PluginManifest, Path]] = []
        for search_path in paths:
            if not search_path.is_dir():
                logger.debug(
                    "plugin_loader.skip_nonexistent_path", path=str(search_path)
                )
                continue
            results.extend(self._scan_directory(search_path))
        return results

    def load(self, manifest: PluginManifest, plugin_dir: Path) -> type[Plugin]:
        """Import the plugin module and return the entry class.

        The entrypoint format is ``"module_path:ClassName"``.

        Args:
            manifest: The plugin manifest with entrypoint info.
            plugin_dir: Directory containing the plugin code.

        Returns:
            The Plugin subclass referenced by the entrypoint.

        Raises:
            PluginLoadError: If the module cannot be imported or the class
                is not a valid Plugin subclass.
        """
        from nahida_bot.plugins.base import Plugin

        entrypoint = manifest.entrypoint
        if ":" not in entrypoint:
            raise PluginLoadError(
                f"Plugin '{manifest.id}' entrypoint must be 'module:Class' "
                f"format, got: '{entrypoint}'"
            )

        module_path, class_name = entrypoint.rsplit(":", 1)

        # Enforce one-module-one-plugin: a module may only be bound to a
        # single plugin to keep unload/reload semantics predictable.
        existing_owner = self._module_to_plugin.get(module_path)
        if existing_owner is not None and existing_owner != manifest.id:
            raise PluginLoadError(
                f"Plugin '{manifest.id}' entrypoint module '{module_path}' is "
                f"already bound to plugin '{existing_owner}'"
            )

        # Ensure plugin_dir is importable
        plugin_dir_str = str(plugin_dir.resolve())
        if plugin_dir_str not in sys.path:
            # FIXME: This mutates process-global import resolution order.
            # Plugin directories inserted at index 0 can shadow unrelated
            # modules and create cross-plugin import side effects.
            sys.path.insert(0, plugin_dir_str)

        try:
            module = importlib.import_module(module_path)
        except ImportError as exc:
            raise PluginLoadError(
                f"Plugin '{manifest.id}' failed to import module '{module_path}': {exc}"
            ) from exc

        # Force reload if already imported (enables hot-reload)
        if module_path in sys.modules:
            # FIXME: First-time load reaches this branch right after
            # import_module(), so module top-level code runs twice
            # (import + reload). Keep reload for explicit hot-reload paths
            # only, not normal load.
            importlib.reload(module)

        entry_class = getattr(module, class_name, None)
        if entry_class is None:
            raise PluginLoadError(
                f"Plugin '{manifest.id}' module '{module_path}' has no "
                f"attribute '{class_name}'"
            )

        if not (isinstance(entry_class, type) and issubclass(entry_class, Plugin)):
            raise PluginLoadError(
                f"Plugin '{manifest.id}' entry class '{class_name}' must be a "
                f"Plugin subclass"
            )

        logger.debug(
            "plugin_loader.loaded_class",
            plugin_id=manifest.id,
            module=module_path,
            class_name=class_name,
        )
        self._module_to_plugin[module_path] = manifest.id
        return entry_class

    def unload(self, manifest: PluginManifest) -> None:
        """Attempt to remove a plugin's module from sys.modules.

        Note: Python's module caching makes full unload difficult. This is a
        best-effort cleanup used during hot-reload scenarios.
        """
        module_path = manifest.entrypoint.rsplit(":", 1)[0]
        sys.modules.pop(module_path, None)
        self._module_to_plugin.pop(module_path, None)
        logger.debug(
            "plugin_loader.unloaded_module",
            plugin_id=manifest.id,
            module=module_path,
        )

    def _scan_directory(self, directory: Path) -> list[tuple[PluginManifest, Path]]:
        """Scan a single directory for plugins."""
        results: list[tuple[PluginManifest, Path]] = []

        # Check if the directory itself is a plugin
        manifest_path = directory / "plugin.yaml"
        if manifest_path.is_file():
            try:
                manifest = parse_manifest(manifest_path)
                results.append((manifest, directory))
            except PluginLoadError:
                logger.warning(
                    "plugin_loader.invalid_manifest",
                    path=str(manifest_path),
                )
            return results

        # Scan subdirectories
        for child in sorted(directory.iterdir()):
            if not child.is_dir():
                continue
            child_manifest = child / "plugin.yaml"
            if child_manifest.is_file():
                try:
                    manifest = parse_manifest(child_manifest)
                    results.append((manifest, child))
                except PluginLoadError:
                    logger.warning(
                        "plugin_loader.invalid_manifest",
                        path=str(child_manifest),
                    )

        return results
