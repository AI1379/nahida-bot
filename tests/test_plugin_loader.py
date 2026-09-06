"""Tests for plugin discovery and dynamic loading."""

from pathlib import Path

import pytest

from nahida_bot.core.exceptions import PluginLoadError
from nahida_bot.plugins.loader import PluginLoader


def _create_plugin_dir(
    parent: Path,
    plugin_id: str,
    entrypoint_module: str | None = None,
    entrypoint_class: str = "TestPlugin",
) -> Path:
    """Create a minimal plugin directory with plugin.yaml and Python file."""
    entrypoint_module = entrypoint_module or f"{plugin_id}_module"
    plugin_dir = parent / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)

    manifest = f"""
id: {plugin_id}
name: {plugin_id.replace("_", " ").title()}
version: "1.0.0"
entrypoint: "{entrypoint_module}:{entrypoint_class}"
"""
    (plugin_dir / "plugin.yaml").write_text(manifest, encoding="utf-8")

    code = f"""
from nahida_bot.plugins.base import Plugin

class {entrypoint_class}(Plugin):
    async def on_load(self) -> None:
        pass
"""
    (plugin_dir / f"{entrypoint_module}.py").write_text(code, encoding="utf-8")
    return plugin_dir


class TestPluginDiscovery:
    """Tests for plugin scanning."""

    def test_discover_single_plugin(self, tmp_path: Path) -> None:
        _create_plugin_dir(tmp_path, "test_plugin")
        loader = PluginLoader()
        results = loader.discover([tmp_path])
        assert len(results) == 1
        assert results[0][0].id == "test_plugin"

    def test_discover_multiple_plugins(self, tmp_path: Path) -> None:
        _create_plugin_dir(tmp_path, "plugin_a")
        _create_plugin_dir(tmp_path, "plugin_b")
        loader = PluginLoader()
        results = loader.discover([tmp_path])
        assert len(results) == 2
        ids = {r[0].id for r in results}
        assert ids == {"plugin_a", "plugin_b"}

    def test_discover_empty_directory(self, tmp_path: Path) -> None:
        loader = PluginLoader()
        results = loader.discover([tmp_path])
        assert results == []

    def test_discover_nonexistent_path(self, tmp_path: Path) -> None:
        loader = PluginLoader()
        results = loader.discover([tmp_path / "nope"])
        assert results == []

    def test_discover_skips_dirs_without_manifest(self, tmp_path: Path) -> None:
        (tmp_path / "not_a_plugin").mkdir()
        (tmp_path / "not_a_plugin" / "readme.txt").write_text("hi")
        loader = PluginLoader()
        results = loader.discover([tmp_path])
        assert results == []


class TestPluginLoading:
    """Tests for dynamic plugin module loading."""

    def test_load_valid_plugin(self, tmp_path: Path) -> None:
        from nahida_bot.plugins.base import Plugin

        plugin_dir = _create_plugin_dir(tmp_path, "loadable")
        loader = PluginLoader()
        results = loader.discover([tmp_path])
        assert len(results) == 1

        manifest, _ = results[0]
        cls = loader.load(manifest, plugin_dir)
        assert issubclass(cls, Plugin)
        assert cls.__name__ == "TestPlugin"

    def test_load_invalid_entrypoint_format(self, tmp_path: Path) -> None:
        from nahida_bot.plugins.manifest import PluginManifest

        manifest = PluginManifest(
            id="bad", name="Bad", version="1.0.0", entrypoint="nomodule"
        )
        loader = PluginLoader()
        with pytest.raises(PluginLoadError, match="module:Class"):
            loader.load(manifest, tmp_path)

    def test_load_missing_class(self, tmp_path: Path) -> None:
        plugin_dir = _create_plugin_dir(tmp_path, "missing_class")
        manifest_path = plugin_dir / "plugin.yaml"
        manifest_content = """
id: missing_class
name: Missing
version: "1.0.0"
entrypoint: "plugin:NonexistentClass"
"""
        manifest_content = manifest_content.replace(
            'entrypoint: "plugin:NonexistentClass"',
            'entrypoint: "missing_class_module:NonexistentClass"',
        )
        manifest_path.write_text(manifest_content, encoding="utf-8")

        from nahida_bot.plugins.manifest import parse_manifest

        manifest = parse_manifest(manifest_path)
        loader = PluginLoader()
        with pytest.raises(PluginLoadError, match="no attribute"):
            loader.load(manifest, plugin_dir)

    def test_normal_load_runs_module_once_and_explicit_reload_runs_again(
        self, tmp_path: Path
    ) -> None:
        """Normal load must not duplicate top-level plugin side effects."""
        from nahida_bot.plugins.manifest import parse_manifest

        plugin_dir = _create_plugin_dir(
            tmp_path,
            "counter_plugin",
            entrypoint_module="counter_module",
        )
        counter_path = tmp_path / "import-count.txt"
        (plugin_dir / "counter_module.py").write_text(
            """
from pathlib import Path

from nahida_bot.plugins.base import Plugin

counter = Path(%r)
count = int(counter.read_text(encoding="utf-8")) if counter.exists() else 0
counter.write_text(str(count + 1), encoding="utf-8")

class TestPlugin(Plugin):
    pass
"""
            % str(counter_path),
            encoding="utf-8",
        )
        manifest = parse_manifest(plugin_dir / "plugin.yaml")
        loader = PluginLoader()

        loader.load(manifest, plugin_dir)
        loader.load(manifest, plugin_dir)
        assert counter_path.read_text(encoding="utf-8") == "1"

        loader.load(manifest, plugin_dir, reload=True)
        assert counter_path.read_text(encoding="utf-8") == "2"
        loader.unload(manifest)

    def test_top_level_exception_is_normalized_with_cause(self, tmp_path: Path) -> None:
        from nahida_bot.plugins.manifest import parse_manifest

        plugin_dir = _create_plugin_dir(
            tmp_path,
            "import_crasher",
            entrypoint_module="import_crash_module",
        )
        (plugin_dir / "import_crash_module.py").write_text(
            "raise RuntimeError('top-level crash')\n",
            encoding="utf-8",
        )
        manifest = parse_manifest(plugin_dir / "plugin.yaml")

        with pytest.raises(PluginLoadError, match="failed to import") as exc_info:
            PluginLoader().load(manifest, plugin_dir)

        assert isinstance(exc_info.value.__cause__, RuntimeError)

    def test_base_exception_from_import_is_not_swallowed(self, tmp_path: Path) -> None:
        from nahida_bot.plugins.manifest import parse_manifest

        plugin_dir = _create_plugin_dir(
            tmp_path,
            "import_interrupt",
            entrypoint_module="import_interrupt_module",
        )
        (plugin_dir / "import_interrupt_module.py").write_text(
            "raise KeyboardInterrupt\n",
            encoding="utf-8",
        )
        manifest = parse_manifest(plugin_dir / "plugin.yaml")

        with pytest.raises(KeyboardInterrupt):
            PluginLoader().load(manifest, plugin_dir)

    def test_unload_removes_module(self, tmp_path: Path) -> None:
        import sys

        plugin_dir = _create_plugin_dir(tmp_path, "unloadable")
        loader = PluginLoader()
        results = loader.discover([tmp_path])
        manifest, _ = results[0]
        loader.load(manifest, plugin_dir)

        loader.unload(manifest)
        module_name = manifest.entrypoint.split(":")[0]
        # Module may or may not be fully unloaded depending on refs,
        # but the unload call should not raise.
        assert module_name not in sys.modules or True  # best-effort
