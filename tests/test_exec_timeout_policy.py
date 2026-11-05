"""Tool budgets follow requested exec time instead of a universal timeout."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from nahida_bot.agent.context import ContextBuilder
from nahida_bot.agent.loop import AgentLoop
from nahida_bot.agent.providers import ProviderResponse, ToolCall
from nahida_bot.core.config import AgentConfig, ExecutionConfig
from nahida_bot.core.exec_runner import ExecResult, ExecRunner
from nahida_bot.core.session_runner import SessionRunner
from nahida_bot.plugins.builtin.commands import BuiltinCommandsPlugin
from nahida_bot.plugins.registry import ToolEntry, ToolRegistry
from nahida_bot.plugins.tool_executor import RegistryToolExecutor
from tests.test_builtin_commands_plugin import _FakeAPI, _manifest
from tests.test_plugin_tool_executor import _ToolCallingProvider


def executor_for(handler, *, overrides=None):
    registry = ToolRegistry()
    registry.register(ToolEntry("exec", "Execute", {}, handler, "builtin-commands"))
    return RegistryToolExecutor(
        registry, exec_runner=ExecRunner(), timeout_overrides=overrides
    )


async def test_exec_outlives_generic_tool_timeout():
    async def long_tool(**kwargs):
        await asyncio.sleep(0.03)
        return "completed"

    executor = executor_for(long_tool)
    loop = AgentLoop(
        provider=_ToolCallingProvider(),
        context_builder=ContextBuilder(),
        tool_executor=executor,
        config=AgentConfig(tool_timeout_seconds=0.01),
    )
    result, _, _ = await loop._execute_tool_with_lifecycle(
        ToolCall("call", "exec", {"command": "long", "timeout": 600})
    )
    assert not result.is_error
    assert (
        executor.timeout_seconds(ToolCall("call", "exec", {"timeout": 600}), 135) == 660
    )


async def test_explicit_tool_override_is_enforced():
    async def long_tool(**kwargs):
        await asyncio.sleep(10)

    executor = executor_for(long_tool, overrides={"exec": 0.01})
    loop = AgentLoop(
        provider=_ToolCallingProvider(),
        context_builder=ContextBuilder(),
        tool_executor=executor,
        config=AgentConfig(tool_retry_attempts=0),
    )
    result, _, _ = await loop._execute_tool_with_lifecycle(
        ToolCall("call", "exec", {"command": "long"})
    )
    assert result.error_code == "tool_timeout"


async def test_stop_long_exec_cleans_process_and_preserves_partial_turn(
    tmp_path, monkeypatch
):
    from tests.test_exec_runner import dead, marker_pid, script

    marker = tmp_path / "long.pid"
    command = script(
        tmp_path,
        f"import os,time; open({str(marker)!r},'w').write(str(os.getpid())); time.sleep(60)",
    )
    runner = ExecRunner(max_concurrency=1, min_available_memory_mb=0)
    api = _FakeAPI()
    api.workspace_root = tmp_path
    api.exec_runner = runner
    plugin = BuiltinCommandsPlugin(api, _manifest())
    registry = ToolRegistry()
    executed = []

    async def execute(command):
        executed.append(command)
        return await plugin._tool_exec(command, timeout=600)

    registry.register(
        ToolEntry(
            "exec",
            "Execute",
            {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
            execute,
            "builtin-commands",
        )
    )

    class Provider(_ToolCallingProvider):
        async def _chat_impl(self, **kwargs):
            self.calls += 1
            return ProviderResponse(
                content="Working",
                tool_calls=[
                    ToolCall("one", "exec", {"command": command}),
                    ToolCall("two", "exec", {"command": "must not run"}),
                ],
            )

    loop = AgentLoop(
        provider=Provider(),
        context_builder=ContextBuilder(),
        tool_executor=RegistryToolExecutor(registry, exec_runner=runner),
        config=AgentConfig(max_steps=1),
    )
    session_runner = SessionRunner(agent_loop=loop, tool_registry=registry)
    persist = AsyncMock()
    monkeypatch.setattr(session_runner, "_persist_turns", persist)
    stop = asyncio.Event()
    task = asyncio.create_task(
        session_runner.run(
            user_message="long job",
            session_id="session",
            system_prompt="sys",
            stop_event=stop,
        )
    )
    try:
        pid = await marker_pid(marker)
        stop.set()
        result = await asyncio.wait_for(task, 10)
        await dead(pid)
        assert not task.cancelled()
        assert result.terminal_state == "cancelled"
        assert len(result.tool_messages) == 2
        assert executed == [command]
        assert runner.admission.snapshot["running"] == 0
        persist.assert_awaited_once()
        persisted_result = persist.call_args.args[2]
        assert persisted_result.terminal_state == "cancelled"
        assert len(persisted_result.tool_messages) == 2
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_exec_handler_accepts_long_requested_timeout_and_configured_default(
    tmp_path,
):
    runner = ExecRunner(default_timeout_seconds=180, max_timeout_seconds=1200)
    runner.run = AsyncMock(return_value=ExecResult(0))
    api = _FakeAPI()
    api.workspace_root = tmp_path
    api.exec_runner = runner
    plugin = BuiltinCommandsPlugin(api, _manifest())
    await plugin._tool_exec("long", timeout=600)
    assert runner.run.call_args.kwargs["timeout"] == 600
    await plugin._tool_exec("default")
    assert runner.run.call_args.kwargs["timeout"] == 180
    plugin._register_exec_tool()
    assert api.tools["exec"]["parameters"]["properties"]["timeout"]["maximum"] == 1200


@pytest.mark.parametrize(
    "config",
    [
        {"queue_timeout_seconds": 0},
        {"queue_timeout_seconds": float("inf")},
        {"max_queued": False},
        {"max_queued_per_owner": 0},
        {"default_timeout_seconds": 1000, "max_timeout_seconds": 100},
    ],
)
def test_invalid_execution_budgets_rejected(config):
    with pytest.raises(ValueError):
        ExecutionConfig(**config)


async def test_background_subagent_gets_own_deadline_instead_of_spawn_tool_budget(
    tmp_path,
):
    from nahida_bot.agent.orchestration import (
        AgentOrchestrator,
        LocalAgentRunExecutor,
        SQLiteBackgroundTaskStore,
        SubagentSpec,
    )
    from nahida_bot.core.context import SessionContext, current_session
    from nahida_bot.core.task_runtime import deadline_scope, remaining_seconds
    from nahida_bot.db.engine import DatabaseEngine
    from tests.test_agent_orchestration import _FakeRunner

    class Runner(_FakeRunner):
        async def run(self, **kwargs):
            assert 0.1 < remaining_seconds() <= 1
            return await super().run(**kwargs)

    engine = DatabaseEngine(tmp_path / "tasks.db")
    await engine.initialize()
    token = current_session.set(
        SessionContext(platform="test", chat_id="chat", session_id="parent")
    )
    try:
        orchestrator = AgentOrchestrator(
            executor=LocalAgentRunExecutor(Runner()),
            task_store=SQLiteBackgroundTaskStore(engine),
        )
        with deadline_scope(0.001):
            task = await orchestrator.spawn_subagent(
                SubagentSpec(task="background", timeout_seconds=1)
            )
        completed = await orchestrator.wait_for_task(task.task_id, timeout_seconds=2)
        assert completed.status.value == "succeeded", completed
    finally:
        current_session.reset(token)
        await engine.close()


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_invalid_tool_timeout_override_rejected(value):
    with pytest.raises(ValueError):
        AgentConfig(tool_timeout_overrides={"some_tool": value})
