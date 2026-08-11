"""Tests for SDK plugin testing helpers and console-capable mocks."""

from __future__ import annotations

import pytest

from nahida_bot_sdk import (
    CommandResult,
    InboundMessage,
    MessagePayload,
    MessageReceived,
    OutboundMessage,
    Plugin,
    PluginManifest,
    register_command,
    register_tool,
    subscribe,
)
from nahida_bot_sdk.testing import (
    ConsoleMockBotAPI,
    RecordingMockBotAPI,
    load_plugin_for_test,
)


class _DecoratedPlugin(Plugin):
    @register_command("hello", description="Say hello", aliases=["hi"])
    async def _cmd_hello(
        self,
        *,
        args: str,
        inbound: InboundMessage,
        session_id: str,
    ) -> CommandResult:
        return CommandResult.text(f"{session_id}:{inbound.user_id}:{args}")

    @register_tool(
        "uppercase",
        description="Uppercase text",
        requires_admin=True,
    )
    async def _tool_uppercase(self, text: str) -> str:
        return text.upper()

    @subscribe(MessageReceived)
    async def _on_message(self, event: MessageReceived) -> None:
        inbound = event.payload.message
        assert isinstance(inbound, InboundMessage)
        await self.api.send_message(
            "console:private:test",
            OutboundMessage(text=f"seen: {inbound.text}"),
        )


def _manifest() -> PluginManifest:
    return PluginManifest(
        id="decorated-test",
        name="Decorated Test",
        version="1.0.0",
        entrypoint="x:Y",
    )


async def _noop_task() -> None:
    pass


def test_decorated_command_rejects_name_alias_conflicts() -> None:
    with pytest.raises(ValueError, match="Duplicate @register_command"):

        class _BadPlugin(Plugin):  # noqa: F841
            @register_command("foo", aliases=["bar"])
            async def _cmd_foo(
                self, *, args: str, inbound: InboundMessage, session_id: str
            ) -> CommandResult:
                return CommandResult.none()

            @register_command("bar")
            async def _cmd_bar(
                self, *, args: str, inbound: InboundMessage, session_id: str
            ) -> CommandResult:
                return CommandResult.none()


async def test_load_plugin_for_test_records_decorated_handlers() -> None:
    api = RecordingMockBotAPI()
    plugin = _DecoratedPlugin(api=api, manifest=_manifest())

    await load_plugin_for_test(plugin)

    assert "hello" in api.registered_commands
    assert "hi" in api.registered_commands
    assert "uppercase" in api.registered_tools
    assert api.registered_tools["uppercase"]["requires_admin"] is True
    assert MessageReceived in api.registered_event_handlers


async def test_console_mock_command_and_event_contracts() -> None:
    api = ConsoleMockBotAPI()
    plugin = _DecoratedPlugin(api=api, manifest=_manifest())

    await load_plugin_for_test(plugin)

    result = await api.invoke_command("hello", "Ada")
    assert result == "console:private:test:console_user:Ada"

    inbound = InboundMessage(
        message_id="m1",
        platform="console",
        chat_id="test",
        user_id="u1",
        text="hello event",
        raw_event={},
    )
    await api._trigger_event(
        MessageReceived(
            payload=MessagePayload(
                message=inbound,
                session_id="console:private:test",
            )
        )
    )

    assert api.sent_messages[0][1].text == "seen: hello event"


def test_recording_mock_records_spawned_tasks_and_closes_coroutines() -> None:
    api = RecordingMockBotAPI()
    coro = _noop_task()

    api.spawn_task("job", coro, kind="oneshot")

    assert api.spawned_tasks["job"]["kind"] == "oneshot"
    assert coro.cr_frame is None
    assert api.cancel_task("job") is True
    assert api.cancel_task("job") is False


def test_console_mock_records_spawned_tasks_and_closes_coroutines() -> None:
    api = ConsoleMockBotAPI()
    coro = _noop_task()

    api.spawn_task("job", coro, kind="oneshot")
    api.spawn_interval_task("ticker", _noop_task, interval_seconds=1.0)

    assert api.spawned_tasks["job"]["kind"] == "oneshot"
    assert api.spawned_tasks["ticker"]["kind"] == "interval"
    assert coro.cr_frame is None
