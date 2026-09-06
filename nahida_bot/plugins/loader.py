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

    def load(
        self,
        manifest: PluginManifest,
        plugin_dir: Path,
        *,
        reload: bool = False,
    ) -> type[Plugin]:
        """Import the plugin module and return the entry class.

        The entrypoint format is ``"module_path:ClassName"``.

        A normal load imports a module once and reuses an already imported
        module.  Callers performing an explicit hot reload may pass
        ``reload=True``; this reloads a module that is already in
        ``sys.modules`` (or imports it when it is not).

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

        # Builtin modules (e.g. nahida_bot.channels.telegram.plugin) are
        # already importable without sys.path manipulation.
        is_builtin = module_path.startswith("nahida_bot.")

        if not is_builtin:
            # Ensure plugin_dir is importable
            plugin_dir_str = str(plugin_dir.resolve())
            if plugin_dir_str not in sys.path:
                # FIXME: This mutates process-global import resolution order.
                # Plugin directories inserted at index 0 can shadow unrelated
                # modules and create cross-plugin import side effects.
                sys.path.insert(0, plugin_dir_str)

        # A separate loader instance can encounter a short external module
        # name (for example ``plugin``) left in sys.modules by an earlier
        # plugin directory.  Loading it from another directory would silently
        # bind the wrong plugin, so report the conflict and require the owner
        # to unload it (or explicitly reload the same module).
        if not is_builtin:
            cached_module = sys.modules.get(module_path)
            if cached_module is not None:
                cached_file = getattr(cached_module, "__file__", None)
                if cached_file is not None:
                    try:
                        cached_path = Path(cached_file).resolve()
                        plugin_root = plugin_dir.resolve()
                    except OSError:
                        pass
                    else:
                        module_parts = module_path.split(".")
                        local_module = plugin_root.joinpath(*module_parts)
                        local_candidates = (
                            local_module.with_suffix(".py"),
                            local_module / "__init__.py",
                        )
                        if any(
                            candidate.is_file() for candidate in local_candidates
                        ) and not cached_path.is_relative_to(plugin_root):
                            raise PluginLoadError(
                                f"Plugin '{manifest.id}' entrypoint module "
                                f"'{module_path}' is already loaded from "
                                f"'{cached_path}'"
                            )

        module_was_loaded = module_path in sys.modules
        action = "reload" if reload and module_was_loaded else "import"
        try:
            if reload and module_was_loaded:
                module = importlib.reload(sys.modules[module_path])
            else:
                module = importlib.import_module(module_path)
        except Exception as exc:  # noqa: BLE001
            # Keep cancellation and process-level exits intact by catching
            # Exception rather than BaseException.  The manager uses this
            # normalized error to transition the record to ERROR.
            raise PluginLoadError(
                f"Plugin '{manifest.id}' failed to {action} module "
                f"'{module_path}': {exc}"
            ) from exc

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

        Builtin modules (``nahida_bot.*``) are never removed from
        ``sys.modules`` because other code may hold direct references to them.
        Removing them would cause ``unittest.mock.patch`` to re-import the
        module as a *new* object, silently breaking any patches that target
        the original module's namespace.
        """
        module_path = manifest.entrypoint.rsplit(":", 1)[0]
        is_builtin = module_path.startswith("nahida_bot.")
        if not is_builtin:
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
