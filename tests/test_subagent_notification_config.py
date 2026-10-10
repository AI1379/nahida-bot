"""End-to-end wiring of YAML notification settings into channel delivery."""

from pathlib import Path
from typing import Literal
from unittest.mock import AsyncMock

import pytest

from nahida_bot.agent.loop import AgentRunResult
from nahida_bot.agent.orchestration import AgentRunStatus, SubagentSpec
from nahida_bot.core.app import Application
from nahida_bot.core.config import ProviderEntryConfig, load_settings
from nahida_bot.core.context import SessionContext, current_session

from .helpers import StubChannelService


@pytest.mark.asyncio
@pytest.mark.parametrize("notifications_enabled", [True, False])
@pytest.mark.parametrize("notify_policy", ["done_only", "silent"])
async def test_yaml_notification_switch_controls_channel_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    notifications_enabled: bool,
    notify_policy: Literal["done_only", "silent"],
) -> None:
    config_path = tmp_path / "config-run.yaml"
    config_path.write_text(
        "agent:\n"
        f"  subagent_notifications_enabled: {str(notifications_enabled).lower()}\n",
        encoding="utf-8",
    )
    settings = load_settings(
        config_yaml=str(config_path),
        db_path=str(tmp_path / "nahida.db"),
        workspace_base_dir=str(tmp_path / "workspace"),
        providers={
            "test": ProviderEntryConfig(
                type="deepseek",
                api_key="test-key",
                base_url="https://example.invalid",
                models=["test-model"],
            )
        },
        default_provider="test",
    )
    app = Application(settings=settings)
    try:
        await app._init_agent_subsystem()
        app._init_scheduler()
        assert app.session_runner is not None
        assert app.orchestration_service is not None
        assert app.memory_store is not None
        monkeypatch.setattr(
            app.session_runner,
            "run",
            AsyncMock(
                return_value=AgentRunResult(
                    final_response="child result", terminal_state="completed"
                )
            ),
        )
        channel = StubChannelService(channel_id="telegram")
        send_message = AsyncMock(return_value="msg_1")
        monkeypatch.setattr(channel, "send_message", send_message)
        app.channel_registry.register(channel)
        session_id = "telegram:private:42"
        await app.memory_store.ensure_session(session_id)
        session_token = current_session.set(
            SessionContext(platform="telegram", chat_id="42", session_id=session_id)
        )
        try:
            task = await app.orchestration_service.spawn_subagent(
                SubagentSpec(task="work", notify_policy=notify_policy)
            )
            completed = await app.orchestration_service.wait_for_task(
                task.task_id, timeout_seconds=1
            )
        finally:
            current_session.reset(session_token)

        assert completed is not None
        assert completed.status == AgentRunStatus.SUCCEEDED
        assert completed.summary == "child result"
        assert send_message.await_count == int(
            notifications_enabled and notify_policy == "done_only"
        )
        records = await app.memory_store.get_recent(session_id)
        assert any(
            record.turn.source == "subagent_completed" for record in records
        ) == (notify_policy == "done_only")
    finally:
        await app.stop()
