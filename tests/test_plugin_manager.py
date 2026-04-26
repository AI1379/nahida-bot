"""Tests for the plugin manager lifecycle."""

from pathlib import Path

import pytest

from nahida_bot.core.config import Settings
from nahida_bot.core.events import (
    EventBus,
    EventContext,
)
from nahida_bot.core.exceptions import PluginStateError
from nahida_bot.plugins.manager import PluginManager, PluginState
from nahida_bot.workspace.manager import WorkspaceManager


def _create_test_plugin(parent: Path, plugin_id: str) -> Path:
    """Create a minimal test plugin directory with a unique module name."""
    plugin_dir = parent / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)

    # Use plugin_id as module name to satisfy one-module-one-plugin rule.
    module_name = f"{plugin_id}_mod"
    manifest = f"""
id: {plugin_id}
name: {plugin_id.replace("_", " ").title()}
version: "1.0.0"
entrypoint: "{module_name}:TestPlugin"
"""
    (plugin_dir / "plugin.yaml").write_text(manifest, encoding="utf-8")

    code = """
from nahida_bot.plugins.base import Plugin

class TestPlugin(Plugin):
    async def on_load(self) -> None:
        pass
"""
    (plugin_dir / f"{module_name}.py").write_text(code, encoding="utf-8")
    return plugin_dir


def _create_crashing_plugin(parent: Path, plugin_id: str) -> Path:
    """Create a plugin that raises on_load, with a unique module name."""
    plugin_dir = parent / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)

    module_name = f"{plugin_id}_mod"
    manifest = f"""
id: {plugin_id}
name: {plugin_id.replace("_", " ").title()}
version: "1.0.0"
entrypoint: "{module_name}:CrashPlugin"
"""
    (plugin_dir / "plugin.yaml").write_text(manifest, encoding="utf-8")

    code = """
from nahida_bot.plugins.base import Plugin

class CrashPlugin(Plugin):
    async def on_load(self) -> None:
        raise RuntimeError("deliberate crash")
"""
    (plugin_dir / f"{module_name}.py").write_text(code, encoding="utf-8")
    return plugin_dir


def _make_event_bus() -> EventBus:
    """Create a minimal EventBus for testing."""
    from unittest.mock import MagicMock

    ctx = EventContext(
        app=MagicMock(),
        settings=Settings(app_name="test"),
        logger=MagicMock(),
    )
    return EventBus(ctx)


class TestPluginDiscovery:
    async def test_discover_finds_plugins(self, tmp_path: Path) -> None:
        _create_test_plugin(tmp_path, "alpha")
        _create_test_plugin(tmp_path, "beta")

        manager = PluginManager(event_bus=_make_event_bus())
        discovered = await manager.discover([tmp_path])

        assert len(discovered) == 2
        ids = {m.id for m in discovered}
        assert ids == {"alpha", "beta"}

    async def test_discover_ignores_known_plugins(self, tmp_path: Path) -> None:
        _create_test_plugin(tmp_path, "alpha")

        manager = PluginManager(event_bus=_make_event_bus())
        await manager.discover([tmp_path])
        discovered = await manager.discover([tmp_path])
        assert discovered == []

    async def test_discover_empty_dir(self, tmp_path: Path) -> None:
        manager = PluginManager(event_bus=_make_event_bus())
        discovered = await manager.discover([tmp_path])
        assert discovered == []


class TestPluginLifecycle:
    async def test_full_lifecycle(self, tmp_path: Path) -> None:
        _create_test_plugin(tmp_path, "lifecycle_test")

        manager = PluginManager(event_bus=_make_event_bus())
        await manager.discover([tmp_path])

        # Load
        await manager.load("lifecycle_test")
        record = manager.get_record("lifecycle_test")
        assert record is not None
        assert record.state == PluginState.LOADED
        assert record.instance is not None

        # Enable
        await manager.enable("lifecycle_test")
        assert record.state == PluginState.ENABLED

        # Disable
        await manager.disable("lifecycle_test")
        assert record.state == PluginState.DISABLED

        # Unload
        await manager.unload("lifecycle_test")
        assert record.state == PluginState.UNLOADED
        assert record.instance is None

    async def test_enable_all_load_all(self, tmp_path: Path) -> None:
        _create_test_plugin(tmp_path, "p1")
        _create_test_plugin(tmp_path, "p2")

        manager = PluginManager(event_bus=_make_event_bus())
        await manager.discover([tmp_path])
        await manager.load_all()
        await manager.enable_all()

        for pid in ("p1", "p2"):
            assert manager.get_record(pid) is not None
            assert manager.get_record(pid).state == PluginState.ENABLED  # type: ignore[union-attr]

    async def test_shutdown_all(self, tmp_path: Path) -> None:
        _create_test_plugin(tmp_path, "p1")
        _create_test_plugin(tmp_path, "p2")

        manager = PluginManager(event_bus=_make_event_bus())
        await manager.discover([tmp_path])
        await manager.load_all()
        await manager.enable_all()
        await manager.shutdown_all()

        for pid in ("p1", "p2"):
            assert manager.get_record(pid).state == PluginState.UNLOADED  # type: ignore[union-attr]


class TestPluginStateTransitions:
    async def test_cannot_enable_found_plugin(self, tmp_path: Path) -> None:
        _create_test_plugin(tmp_path, "state_test")
        manager = PluginManager(event_bus=_make_event_bus())
        await manager.discover([tmp_path])

        with pytest.raises(PluginStateError, match="expected loaded or disabled"):
            await manager.enable("state_test")

    async def test_cannot_load_unknown_plugin(self) -> None:
        manager = PluginManager(event_bus=_make_event_bus())
        with pytest.raises(PluginStateError, match="not discovered"):
            await manager.load("nonexistent")

    async def test_cannot_double_load(self, tmp_path: Path) -> None:
        _create_test_plugin(tmp_path, "double")
        manager = PluginManager(event_bus=_make_event_bus())
        await manager.discover([tmp_path])
        await manager.load("double")

        with pytest.raises(PluginStateError, match="expected found"):
            await manager.load("double")


class TestPluginExceptionIsolation:
    async def test_crashing_plugin_goes_to_error_state(self, tmp_path: Path) -> None:
        _create_crashing_plugin(tmp_path, "crasher")

        manager = PluginManager(event_bus=_make_event_bus())
        await manager.discover([tmp_path])
        await manager.load("crasher")

        # enable calls on_load which crashes -> ERROR state
        await manager.enable("crasher")
        record = manager.get_record("crasher")
        assert record is not None
        assert record.state == PluginState.ERROR

    async def test_one_plugin_crash_does_not_affect_others(
        self, tmp_path: Path
    ) -> None:
        _create_crashing_plugin(tmp_path, "crasher")
        _create_test_plugin(tmp_path, "healthy")

        manager = PluginManager(event_bus=_make_event_bus())
        await manager.discover([tmp_path])
        await manager.load_all()
        await manager.enable_all()

        assert manager.get_record("crasher").state == PluginState.ERROR  # type: ignore[union-attr]
        assert manager.get_record("healthy").state == PluginState.ENABLED  # type: ignore[union-attr]


class TestPluginToolRegistration:
    async def test_tool_registered_on_enable(self, tmp_path: Path) -> None:
        """Plugin that registers a tool via api_bridge."""
        plugin_dir = tmp_path / "tool_plugin"
        plugin_dir.mkdir(parents=True, exist_ok=True)

        manifest = """
id: tool_plugin
name: Tool Plugin
version: "1.0.0"
entrypoint: "plugin:ToolPlugin"
"""
        (plugin_dir / "plugin.yaml").write_text(manifest, encoding="utf-8")

        code = """
from nahida_bot.plugins.base import Plugin

class ToolPlugin(Plugin):
    async def on_load(self) -> None:
        self.api.register_tool(
            "my_tool",
            "A test tool",
            {"type": "object", "properties": {"query": {"type": "string"}}},
            self._handle,
        )

    async def _handle(self, query: str) -> str:
        return f"result: {query}"
"""
        (plugin_dir / "plugin.py").write_text(code, encoding="utf-8")

        manager = PluginManager(event_bus=_make_event_bus())
        await manager.discover([tmp_path])
        await manager.load("tool_plugin")
        await manager.enable("tool_plugin")

        entry = manager.tool_registry.get("my_tool")
        assert entry is not None
        assert entry.plugin_id == "tool_plugin"

        # Disable should remove the tool
        await manager.disable("tool_plugin")
        assert manager.tool_registry.get("my_tool") is None

    async def test_builtin_workspace_tools_are_registered_and_execute(
        self, tmp_path: Path
    ) -> None:
        import nahida_bot.plugins.builtin as builtin_pkg

        workspace = WorkspaceManager(tmp_path / "workspace")
        workspace.initialize()
        builtin_file = builtin_pkg.__file__
        assert builtin_file is not None
        builtin_path = Path(builtin_file).parent

        manager = PluginManager(
            event_bus=_make_event_bus(),
            workspace_manager=workspace,
        )
        await manager.discover([builtin_path])
        await manager.load("builtin-commands")
        await manager.enable("builtin-commands")

        write_tool = manager.tool_registry.get("workspace_write")
        read_tool = manager.tool_registry.get("workspace_read")
        assert write_tool is not None
        assert read_tool is not None

        await write_tool.handler(path="notes/hello.txt", content="hello workspace")
        result = await read_tool.handler(path="notes/hello.txt")

        assert result == "hello workspace"
