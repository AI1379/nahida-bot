"""Tests for the plugin manager lifecycle."""

from pathlib import Path

import pytest

from nahida_bot.core.config import Settings
from nahida_bot.core.events import (
    EventBus,
    EventContext,
)
from nahida_bot.core.exceptions import PluginStateError
from nahida_bot.core.tasks import TaskManager
from nahida_bot.plugins.manager import PluginManager, PluginState
from nahida_bot.workspace.manager import WorkspaceManager


class _ChannelRegistry:
    def __init__(self) -> None:
        self.channels: dict[str, object] = {}

    def register(self, channel: object) -> None:
        self.channels[channel.channel_id] = channel  # type: ignore[attr-defined]

    def unregister(self, channel_id: str) -> None:
        self.channels.pop(channel_id, None)

    def get(self, channel_id: str) -> object | None:
        return self.channels.get(channel_id)


def _create_test_plugin(
    parent: Path,
    plugin_id: str,
    *,
    load_phase: str = "post-agent",
    enabled: bool = True,
) -> Path:
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
load_phase: "{load_phase}"
enabled: {str(enabled).lower()}
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
        assert record.instance is None
        assert record.api_bridge is None

        # Administrators may still load without activating, then unload.
        await manager.load("lifecycle_test")
        assert record.state == PluginState.LOADED
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

    async def test_late_loaded_plugin_receives_document_store_manager(
        self, tmp_path: Path
    ) -> None:
        plugin_dir = _create_test_plugin(tmp_path, "storage_plugin")
        manifest_path = plugin_dir / "plugin.yaml"
        manifest_path.write_text(
            manifest_path.read_text(encoding="utf-8")
            + "\npermissions:\n  llm_access: true\n",
            encoding="utf-8",
        )
        document_store_manager = object()
        manager = PluginManager(event_bus=_make_event_bus())
        manager.set_runtime_services(
            document_store_manager=document_store_manager,
        )

        await manager.discover([tmp_path])
        await manager.load("storage_plugin")

        record = manager.get_record("storage_plugin")
        assert record is not None
        assert record.api_bridge is not None
        assert record.api_bridge.get_document_store_manager() is document_store_manager

    async def test_load_all_and_enable_all_can_filter_by_phase(
        self, tmp_path: Path
    ) -> None:
        _create_test_plugin(tmp_path, "pre_plugin", load_phase="pre-agent")
        _create_test_plugin(tmp_path, "post_plugin", load_phase="post-agent")

        manager = PluginManager(event_bus=_make_event_bus())
        await manager.discover([tmp_path])

        await manager.load_all(phase="pre-agent")
        await manager.enable_all(phase="pre-agent")

        assert manager.get_record("pre_plugin").state == PluginState.ENABLED  # type: ignore[union-attr]
        assert manager.get_record("post_plugin").state == PluginState.FOUND  # type: ignore[union-attr]

        await manager.load_all(phase="post-agent")
        await manager.enable_all(phase="post-agent")

        assert manager.get_record("post_plugin").state == PluginState.ENABLED  # type: ignore[union-attr]

    async def test_shutdown_all(self, tmp_path: Path) -> None:
        _create_test_plugin(tmp_path, "p1")
        _create_test_plugin(tmp_path, "p2")

        manager = PluginManager(event_bus=_make_event_bus())
        await manager.discover([tmp_path])
        await manager.load_all()
        await manager.enable_all()
        await manager.shutdown_all()

        for pid in ("p1", "p2"):
            record = manager.get_record(pid)
            assert record is not None
            assert record.state == PluginState.DISABLED
            assert record.instance is None

    async def test_reenable_restores_imperative_registrations(
        self, tmp_path: Path
    ) -> None:
        plugin_dir = tmp_path / "imperative_lifecycle"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        manifest = """
id: imperative_lifecycle
name: Imperative Lifecycle
version: "1.0.0"
entrypoint: "plugin:LifecyclePlugin"
"""
        (plugin_dir / "plugin.yaml").write_text(manifest, encoding="utf-8")
        code = """
from nahida_bot.core.events import Event
from nahida_bot.plugins.base import Plugin

class LifecyclePlugin(Plugin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.on_load_count = 0
        self.on_enable_count = 0
        self.on_disable_count = 0

    async def on_load(self) -> None:
        self.on_load_count += 1
        self.api.register_command("hello", self._cmd_hello)
        self.api.register_tool("echo_tool", "Echo", {"type": "object"}, self._tool_echo)
        self.api.subscribe(Event, self._on_event)

    async def on_enable(self) -> None:
        self.on_enable_count += 1

    async def on_disable(self) -> None:
        self.on_disable_count += 1

    async def _cmd_hello(self, *, args, inbound, session_id):
        return "hello"

    async def _tool_echo(self, **kwargs):
        return "ok"

    async def _on_event(self, event):
        return None
"""
        (plugin_dir / "plugin.py").write_text(code, encoding="utf-8")

        manager = PluginManager(event_bus=_make_event_bus())
        await manager.discover([tmp_path])
        await manager.load("imperative_lifecycle")
        await manager.enable("imperative_lifecycle")

        record = manager.get_record("imperative_lifecycle")
        assert record is not None
        assert manager.command_registry.get("hello") is not None
        assert manager.tool_registry.get("echo_tool") is not None
        assert (
            len(manager.handler_registry.handlers_for_plugin("imperative_lifecycle"))
            == 1
        )

        first_instance = record.instance
        assert first_instance is not None

        await manager.disable("imperative_lifecycle")
        assert manager.command_registry.get("hello") is None
        assert manager.tool_registry.get("echo_tool") is None
        assert (
            manager.handler_registry.handlers_for_plugin("imperative_lifecycle") == []
        )

        await manager.enable("imperative_lifecycle")
        assert manager.command_registry.get("hello") is not None
        assert manager.tool_registry.get("echo_tool") is not None
        assert (
            len(manager.handler_registry.handlers_for_plugin("imperative_lifecycle"))
            == 1
        )

        instance = record.instance
        assert instance is not None
        assert instance is not first_instance
        assert instance.on_load_count == 1  # type: ignore[attr-defined]
        assert instance.on_enable_count == 1  # type: ignore[attr-defined]
        assert first_instance.on_disable_count == 1  # type: ignore[attr-defined]

    async def test_reenable_restores_decorator_registrations(
        self, tmp_path: Path
    ) -> None:
        plugin_dir = tmp_path / "decorator_lifecycle"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        manifest = """
id: decorator_lifecycle
name: Decorator Lifecycle
version: "1.0.0"
entrypoint: "plugin:DecoratorPlugin"
"""
        (plugin_dir / "plugin.yaml").write_text(manifest, encoding="utf-8")
        code = """
from nahida_bot.core.events import Event
from nahida_bot.plugins.base import Plugin
from nahida_bot_sdk import register_command, register_tool, subscribe

class DecoratorPlugin(Plugin):
    @register_command("decorated")
    async def _cmd_decorated(self, *, args, inbound, session_id):
        return "ok"

    @register_tool(
        "decorated_tool",
        description="Decorated",
        requires_admin=True,
    )
    async def _tool_decorated(self, **kwargs):
        return "ok"

    @subscribe(Event)
    async def _on_event(self, event):
        return None
"""
        (plugin_dir / "plugin.py").write_text(code, encoding="utf-8")

        manager = PluginManager(event_bus=_make_event_bus())
        await manager.discover([tmp_path])
        await manager.load("decorator_lifecycle")
        await manager.enable("decorator_lifecycle")

        assert manager.command_registry.get("decorated") is not None
        decorated_tool = manager.tool_registry.get("decorated_tool")
        assert decorated_tool is not None
        assert decorated_tool.requires_admin is True
        assert (
            len(manager.handler_registry.handlers_for_plugin("decorator_lifecycle"))
            == 1
        )

        await manager.disable("decorator_lifecycle")
        assert manager.command_registry.get("decorated") is None
        assert manager.tool_registry.get("decorated_tool") is None
        assert manager.handler_registry.handlers_for_plugin("decorator_lifecycle") == []

        await manager.enable("decorator_lifecycle")
        assert manager.command_registry.get("decorated") is not None
        decorated_tool = manager.tool_registry.get("decorated_tool")
        assert decorated_tool is not None
        assert decorated_tool.requires_admin is True
        assert (
            len(manager.handler_registry.handlers_for_plugin("decorator_lifecycle"))
            == 1
        )


class TestPluginStateTransitions:
    async def test_enable_found_plugin_loads_and_activates_it(
        self, tmp_path: Path
    ) -> None:
        _create_test_plugin(tmp_path, "state_test")
        manager = PluginManager(event_bus=_make_event_bus())
        await manager.discover([tmp_path])

        await manager.enable("state_test")

        record = manager.get_record("state_test")
        assert record is not None
        assert record.state == PluginState.ENABLED

    async def test_configured_disabled_plugin_is_not_loaded(
        self, tmp_path: Path
    ) -> None:
        _create_test_plugin(tmp_path, "disabled_test", enabled=False)
        manager = PluginManager(event_bus=_make_event_bus())
        await manager.discover([tmp_path])

        await manager.load_all()
        await manager.enable_all()

        record = manager.get_record("disabled_test")
        assert record is not None
        assert record.configured_enabled is False
        assert record.state == PluginState.DISABLED
        assert record.instance is None

    async def test_reload_retries_plugin_in_error_state(self, tmp_path: Path) -> None:
        _create_crashing_plugin(tmp_path, "reload_error")
        manager = PluginManager(event_bus=_make_event_bus())
        await manager.discover([tmp_path])
        await manager.enable("reload_error")
        record = manager.get_record("reload_error")
        assert record is not None
        assert record.state == PluginState.ERROR

        await manager.reload("reload_error")

        assert record.state == PluginState.ERROR
        assert "deliberate crash" in record.error_message

    async def test_framework_enabled_is_removed_from_plugin_config(
        self, tmp_path: Path
    ) -> None:
        _create_test_plugin(tmp_path, "configured_test", enabled=False)
        manager = PluginManager(event_bus=_make_event_bus())
        await manager.discover([tmp_path])

        manager.apply_config(
            "configured_test",
            {"enabled": True, "business_value": "ok"},
        )

        record = manager.get_record("configured_test")
        assert record is not None
        assert record.configured_enabled is True
        assert record.state == PluginState.FOUND
        assert record.manifest.config == {"business_value": "ok"}

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

    async def test_on_load_failure_stops_enable_and_clears_registrations(
        self, tmp_path: Path
    ) -> None:
        from nahida_bot.agent.providers.registry import (
            clear_runtime_providers,
            create_provider,
        )

        plugin_dir = tmp_path / "registering_crasher"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        provider_type = "failed-runtime-provider"
        clear_runtime_providers(owner_plugin_id="registering_crasher")

        manifest = """
id: registering_crasher
name: Registering Crasher
version: "1.0.0"
load_phase: "pre-agent"
entrypoint: "plugin:RegisteringCrashPlugin"
permissions:
  network:
    inbound: true
"""
        (plugin_dir / "plugin.yaml").write_text(manifest, encoding="utf-8")

        code = f'''
from nahida_bot.plugins.base import Plugin

class _FailedChannel:
    channel_id = "failed-channel"

    async def handle_inbound_event(self, event):
        return None

    async def send_message(self, target, message):
        return "failed-message-id"

class RegisteringCrashPlugin(Plugin):
    async def on_load(self) -> None:
        self.api.register_channel(_FailedChannel())
        self.api.register_provider_type(
            "{provider_type}",
            lambda config: None,
        )
        self.api.register_tool(
            "failed_tool",
            "Should be cleaned",
            {{"type": "object"}},
            self._handle,
        )
        raise RuntimeError("deliberate crash")

    async def on_enable(self) -> None:
        raise AssertionError("on_enable should not run after on_load failure")

    async def _handle(self) -> str:
        return "unused"
'''
        (plugin_dir / "plugin.py").write_text(code, encoding="utf-8")

        channel_registry = _ChannelRegistry()
        manager = PluginManager(
            event_bus=_make_event_bus(),
            channel_registry=channel_registry,
        )

        try:
            await manager.discover([tmp_path])
            await manager.load("registering_crasher")
            await manager.enable("registering_crasher")

            record = manager.get_record("registering_crasher")
            assert record is not None
            assert record.state == PluginState.ERROR
            assert "deliberate crash" in record.error_message
            assert channel_registry.get("failed-channel") is None
            assert manager.tool_registry.get("failed_tool") is None
            with pytest.raises(ValueError, match="Unknown provider type"):
                create_provider(provider_type)
        finally:
            clear_runtime_providers(owner_plugin_id="registering_crasher")

    async def test_channel_plugins_are_not_auto_registered(
        self, tmp_path: Path
    ) -> None:
        plugin_dir = tmp_path / "passive_channel"
        plugin_dir.mkdir(parents=True, exist_ok=True)

        manifest = """
id: passive_channel
name: Passive Channel
version: "1.0.0"
entrypoint: "plugin:PassiveChannel"
"""
        (plugin_dir / "plugin.yaml").write_text(manifest, encoding="utf-8")

        code = """
from nahida_bot.plugins.base import Plugin

class PassiveChannel(Plugin):
    @property
    def channel_id(self) -> str:
        return self.manifest.id

    async def on_load(self) -> None:
        pass

    async def handle_inbound_event(self, event: dict) -> None:
        pass

    async def send_message(self, target: str, message) -> str:
        return ""
"""
        (plugin_dir / "plugin.py").write_text(code, encoding="utf-8")

        channel_registry = _ChannelRegistry()
        manager = PluginManager(
            event_bus=_make_event_bus(),
            channel_registry=channel_registry,
        )
        await manager.discover([tmp_path])
        await manager.load("passive_channel")
        await manager.enable("passive_channel")

        assert manager.get_record("passive_channel").state == PluginState.ENABLED  # type: ignore[union-attr]
        assert channel_registry.get("passive_channel") is None


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

    async def test_tool_registered_on_enable_can_reenable(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "enable_tool_plugin"
        plugin_dir.mkdir(parents=True, exist_ok=True)

        manifest = """
id: enable_tool_plugin
name: Enable Tool Plugin
version: "1.0.0"
entrypoint: "plugin:EnableToolPlugin"
"""
        (plugin_dir / "plugin.yaml").write_text(manifest, encoding="utf-8")

        code = """
from nahida_bot.plugins.base import Plugin

class EnableToolPlugin(Plugin):
    async def on_enable(self) -> None:
        self.api.register_tool(
            "cycle_tool",
            "A re-enabled test tool",
            {"type": "object", "properties": {"query": {"type": "string"}}},
            self._handle,
        )

    async def _handle(self, query: str) -> str:
        return f"result: {query}"
"""
        (plugin_dir / "plugin.py").write_text(code, encoding="utf-8")

        manager = PluginManager(event_bus=_make_event_bus())
        await manager.discover([tmp_path])
        await manager.load("enable_tool_plugin")
        await manager.enable("enable_tool_plugin")

        assert manager.tool_registry.get("cycle_tool") is not None

        await manager.disable("enable_tool_plugin")
        assert manager.tool_registry.get("cycle_tool") is None

        await manager.enable("enable_tool_plugin")

        record = manager.get_record("enable_tool_plugin")
        assert record is not None
        assert record.state == PluginState.ENABLED
        entry = manager.tool_registry.get("cycle_tool")
        assert entry is not None
        assert entry.plugin_id == "enable_tool_plugin"

    async def test_spawned_task_can_reenable_with_same_name(
        self, tmp_path: Path
    ) -> None:
        plugin_dir = tmp_path / "task_plugin"
        plugin_dir.mkdir(parents=True, exist_ok=True)

        manifest = """
id: task_plugin
name: Task Plugin
version: "1.0.0"
entrypoint: "plugin:TaskPlugin"
"""
        (plugin_dir / "plugin.yaml").write_text(manifest, encoding="utf-8")

        code = """
import asyncio

from nahida_bot.plugins.base import Plugin

class TaskPlugin(Plugin):
    async def on_enable(self) -> None:
        self.api.spawn_task("worker", self._worker())

    async def _worker(self) -> None:
        await asyncio.Event().wait()
"""
        (plugin_dir / "plugin.py").write_text(code, encoding="utf-8")

        task_manager = TaskManager()
        manager = PluginManager(event_bus=_make_event_bus(), task_manager=task_manager)
        await manager.discover([tmp_path])
        await manager.load("task_plugin")
        await manager.enable("task_plugin")

        info = task_manager.get_task("task_plugin:worker")
        assert info is not None
        assert info.status == "running"

        await manager.disable("task_plugin")
        info = task_manager.get_task("task_plugin:worker")
        assert info is not None
        assert info.status == "cancelled"

        await manager.enable("task_plugin")

        record = manager.get_record("task_plugin")
        assert record is not None
        assert record.state == PluginState.ENABLED
        info = task_manager.get_task("task_plugin:worker")
        assert info is not None
        assert info.status == "running"

        await manager.disable("task_plugin")

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
