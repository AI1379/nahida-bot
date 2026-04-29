"""Tests for the builtin commands and workspace tools plugin."""

from __future__ import annotations

from typing import Any

import pytest

from nahida_bot.plugins.base import InboundMessage, MemoryRef, OutboundMessage
from nahida_bot.plugins.builtin.commands import BuiltinCommandsPlugin
from nahida_bot.plugins.commands import CommandEntry, CommandRegistry
from nahida_bot.plugins.manifest import PluginManifest


def _manifest() -> PluginManifest:
    return PluginManifest(
        id="builtin-commands",
        name="Builtin Commands",
        version="0.1.0",
        entrypoint="nahida_bot.plugins.builtin.commands:BuiltinCommandsPlugin",
    )


def _inbound() -> InboundMessage:
    return InboundMessage(
        message_id="m1",
        platform="telegram",
        chat_id="c1",
        user_id="u1",
        text="/help",
        raw_event={},
    )


class _FakeAPI:
    def __init__(self) -> None:
        self.commands: dict[str, Any] = {}
        self.tools: dict[str, Any] = {}
        self.files: dict[str, str] = {}
        self.cleared: list[str] = []
        self.new_sessions: list[tuple[str, str]] = []
        self.session_meta: dict[str, Any] = {}
        self.models = [
            {"provider_id": "p1", "model": "model-a"},
            {"provider_id": "p2", "model": "model-b"},
        ]
        self.command_registry = CommandRegistry()

    def register_command(self, name: str, handler: Any, **kwargs: Any) -> None:
        self.commands[name] = (handler, kwargs)

    async def send_message(
        self, target: str, message: OutboundMessage, *, channel: str = ""
    ) -> str:
        return "msg-1"

    def on_event(self, event_type: type) -> Any:
        return lambda handler: handler

    def subscribe(self, event_type: type, handler: Any) -> Any:
        return None

    def register_tool(
        self, name: str, description: str, parameters: dict[str, Any], handler: Any
    ) -> None:
        self.tools[name] = {
            "description": description,
            "parameters": parameters,
            "handler": handler,
        }

    async def workspace_read(self, path: str) -> str:
        return self.files[path]

    async def workspace_write(self, path: str, content: str) -> None:
        self.files[path] = content

    async def get_session(self, session_id: str) -> Any:
        return None

    async def memory_search(self, query: str, *, limit: int = 5) -> list[MemoryRef]:
        return []

    async def memory_store(
        self, key: str, content: str, *, metadata: dict[str, Any] | None = None
    ) -> None:
        pass

    async def publish_event(self, event: Any) -> None:
        pass

    @property
    def logger(self) -> Any:
        return None

    async def clear_session(self, session_id: str) -> int:
        self.cleared.append(session_id)
        return 2

    async def start_new_session(self, platform: str, chat_id: str) -> str | None:
        self.new_sessions.append((platform, chat_id))
        return f"{platform}:{chat_id}:abc12345"

    def list_models(self) -> list[dict[str, str]]:
        return self.models

    async def set_session_model(self, session_id: str, model_name: str) -> str | None:
        if model_name == "model-b":
            self.session_meta = {"provider_id": "p2", "model": model_name}
            return "p2"
        return None

    async def get_session_info(self, session_id: str) -> dict[str, Any]:
        return dict(self.session_meta)

    def list_commands(self) -> list[Any]:
        return [entry.to_info() for entry in self.command_registry.all_commands()]


@pytest.mark.asyncio
async def test_on_load_registers_commands_and_workspace_tools() -> None:
    api = _FakeAPI()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())

    await plugin.on_load()

    assert {"reset", "new", "status", "model", "help"} <= set(api.commands)
    assert {"workspace_read", "workspace_write"} <= set(api.tools)
    assert api.tools["workspace_read"]["parameters"]["required"] == ["path"]
    assert api.tools["workspace_write"]["parameters"]["required"] == [
        "path",
        "content",
    ]


@pytest.mark.asyncio
async def test_workspace_tools_delegate_to_bot_api() -> None:
    api = _FakeAPI()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())

    result = await plugin._tool_workspace_write("notes/a.txt", "hello")
    assert result == "Written workspace file: notes/a.txt"
    assert await plugin._tool_workspace_read("notes/a.txt") == "hello"


@pytest.mark.asyncio
async def test_reset_status_model_and_help_commands() -> None:
    async def _help_handler(**kwargs: object) -> str:
        return "ok"

    api = _FakeAPI()
    api.session_meta = {"provider_id": "p1", "model": "model-a"}
    api.command_registry.register(
        CommandEntry(
            name="help",
            handler=_help_handler,
            description="Show help",
            aliases=("h",),
            plugin_id="builtin-commands",
        )
    )
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())

    assert await plugin._cmd_reset(args="", inbound=_inbound(), session_id="s1") == (
        "Session cleared. 2 message(s) removed."
    )
    status = await plugin._cmd_status(args="", inbound=_inbound(), session_id="s1")
    assert "Provider: p1" in status
    assert "Model: model-a" in status
    model_list = await plugin._cmd_model(args="", inbound=_inbound(), session_id="s1")
    assert "p1/model-a (current)" in model_list
    switched = await plugin._cmd_model(
        args="model-b", inbound=_inbound(), session_id="s1"
    )
    assert switched == "Switched to model-b (via p2)"
    missing = await plugin._cmd_model(
        args="missing", inbound=_inbound(), session_id="s1"
    )
    assert missing == "Model 'missing' not found in any provider."
    help_text = await plugin._cmd_help(args="", inbound=_inbound(), session_id="s1")
    assert "/help (h)" in help_text
    assert "Show help" in help_text


@pytest.mark.asyncio
async def test_new_command_switches_router_session() -> None:
    api = _FakeAPI()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())

    result = await plugin._cmd_new(args="", inbound=_inbound(), session_id="old")

    assert result == "New session started: telegram:c1:abc12345"
    assert api.new_sessions == [("telegram", "c1")]
