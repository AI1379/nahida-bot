"""Application wiring, tool failures, and cron/exec budget sharing."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest

from nahida_bot.agent.providers import ToolCall
from nahida_bot.core.app import Application
from nahida_bot.core.config import ExecutionConfig, Settings
from nahida_bot.core.exec_runner import ExecCommandError, ExecResult, ExecRunner
from nahida_bot.plugins.builtin.commands import BuiltinCommandsPlugin
from nahida_bot.plugins.manager import PluginManager
from nahida_bot.plugins.registry import ToolEntry, ToolRegistry
from nahida_bot.plugins.tool_executor import RegistryToolExecutor
from nahida_bot.scheduler.script_executor import execute_script, render_fallback_context
from tests.test_builtin_commands_plugin import _FakeAPI, _manifest
from tests.test_scheduler import _job


@pytest.mark.parametrize(
    "config",
    [
        {"memory_limit_mb": 0},
        {"max_concurrency": True},
        {"check_interval_seconds": float("nan")},
        {"unknown_option": 1},
    ],
)
def test_execution_configuration_rejects_invalid_limits(config):
    with pytest.raises(ValueError):
        ExecutionConfig(**config)


async def test_application_injects_one_runner_into_plugin_and_scheduler(tmp_path):
    app = Application(Settings(execution=ExecutionConfig(memory_limit_mb=123)))
    app._db_engine = MagicMock()
    app._media_store = MagicMock()
    app.document_store_manager = None
    app._plugin_data_repo = None
    app._plugin_secret_repo = None
    app.plugin_manager = PluginManager(
        event_bus=app.event_bus, exec_runner=app.exec_runner
    )
    app._init_scheduler()
    assert app.scheduler_service._exec_runner is app.exec_runner
    plugin_dir = tmp_path / "builtin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.yaml").write_text(
        'id: builtin-commands\nname: Builtin\nversion: "1"\n'
        "entrypoint: nahida_bot.plugins.builtin.commands:BuiltinCommandsPlugin\n"
        "permissions:\n  system:\n    subprocess: true\n",
        encoding="utf-8",
    )
    await app.plugin_manager.discover([tmp_path])
    try:
        await app.plugin_manager.load("builtin-commands")
        record = app.plugin_manager.get_record("builtin-commands")
        assert record.instance._exec_runner is app.exec_runner
        assert record.instance._exec_runner.memory_limit_mb == 123
    finally:
        await app.plugin_manager.shutdown_all()


@pytest.mark.parametrize("first", ["exec", "cron"])
async def test_exec_and_cron_share_concurrency_budget(tmp_path, monkeypatch, first):
    runner = ExecRunner(min_available_memory_mb=0)
    api = _FakeAPI()
    api.workspace_root = tmp_path
    api.exec_runner = runner
    plugin = BuiltinCommandsPlugin(api, _manifest())
    job = replace(_job(), script_command="test command", script_timeout_seconds=5)
    entered, release = asyncio.Event(), asyncio.Event()

    async def running(*args, **kwargs):
        entered.set()
        await release.wait()
        return ExecResult(0)

    monkeypatch.setattr(runner, "_run", running)
    active = asyncio.create_task(
        plugin._tool_exec("first")
        if first == "exec"
        else execute_script(job, exec_runner=runner)
    )
    await entered.wait()
    try:
        if first == "exec":
            result = await execute_script(job, exec_runner=runner)
            assert not result.succeeded
            assert result.termination_reason == "busy"
        else:
            with pytest.raises(ExecCommandError) as caught:
                await plugin._tool_exec("second")
            assert caught.value.code == "busy"
    finally:
        active.cancel()
        await asyncio.gather(active, return_exceptions=True)
    release.set()
    assert (await execute_script(job, exec_runner=runner)).succeeded


async def test_memory_failure_remains_error_through_registry_and_cron(tmp_path):
    runner = ExecRunner()
    runner.run = AsyncMock(
        return_value=ExecResult(
            1,
            termination_reason="memory_limit_exceeded",
            peak_rss_bytes=600 * 1024 * 1024,
        )
    )
    api = _FakeAPI()
    api.workspace_root = tmp_path
    api.exec_runner = runner
    plugin = BuiltinCommandsPlugin(api, _manifest())
    registry = ToolRegistry()
    registry.register(
        ToolEntry("exec", "Execute", {}, plugin._tool_exec, "builtin-commands")
    )
    result = await RegistryToolExecutor(registry).execute(
        ToolCall("id", "exec", {"command": "heavy"})
    )
    assert result.is_error and not result.retryable
    assert result.error_code == "memory_limit_exceeded"
    assert "Resize images" in result.error_message
    job = replace(_job(), script_command="heavy")
    cron_result = await execute_script(job, exec_runner=runner)
    assert not cron_result.succeeded
    context = render_fallback_context(job, cron_result)
    assert '"termination_reason": "memory_limit_exceeded"' in context
    assert "do not retry" in context
