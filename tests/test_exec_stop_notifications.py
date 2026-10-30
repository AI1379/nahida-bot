"""Shutdown cancellation must not be presented as an unexplained user stop."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nahida_bot.agent.loop import LoopEvent
from nahida_bot.core.router import _AgentDeliveryState, ReasoningDisplayConfig
from tests.test_message_router import _make_router, _inbound
from tests.test_builtin_commands_plugin import _FakeAPI, _manifest
from nahida_bot.core.exec_runner import ExecCommandError, ExecResult
from nahida_bot.plugins.builtin.commands import BuiltinCommandsPlugin


@pytest.mark.parametrize("shutdown", [False, True])
async def test_cancelled_response_distinguishes_service_shutdown(shutdown):
    router, _, _, _ = _make_router()
    router._stopping = shutdown
    router._send_response = AsyncMock()
    state = _AgentDeliveryState()
    request = SimpleNamespace(
        inbound=_inbound(), session_id="test:private:c1", reply_to_override=None
    )
    await router._handle_agent_done(
        request,
        LoopEvent(type="done", error="cancelled"),
        ReasoningDisplayConfig(show=False, max_chars=2000),
        state,
    )
    text = router._send_response.call_args.args[2]
    assert state.cancelled
    if shutdown:
        assert "Service is shutting down" in text
        assert "Agent stopped" not in text
    else:
        assert text == "[Agent] Stopped"


async def test_memory_limit_has_actionable_tool_result(tmp_path):
    api = _FakeAPI()
    api.workspace_root = tmp_path
    plugin = BuiltinCommandsPlugin(api, _manifest())
    plugin._exec_runner.run = AsyncMock(
        return_value=ExecResult(137, "", "", termination_reason="memory_limit_exceeded")
    )
    with pytest.raises(ExecCommandError) as caught:
        await plugin._tool_exec("heavy image task")
    assert caught.value.code == "memory_limit_exceeded"
    output = str(caught.value)
    assert "512 MiB memory limit" in output
    assert "Resize images" in output


async def test_exec_cancellation_propagates(tmp_path):
    api = _FakeAPI()
    api.workspace_root = tmp_path
    plugin = BuiltinCommandsPlugin(api, _manifest())
    plugin._exec_runner.run = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        await plugin._tool_exec("interrupted task")
