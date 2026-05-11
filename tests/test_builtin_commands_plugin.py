"""Tests for the builtin commands and workspace tools plugin."""

from __future__ import annotations

from typing import Any

import pytest

from nahida_bot.core.context import SessionContext, current_session
from nahida_bot.plugins.base import InboundMessage, MemoryRef, OutboundMessage
from nahida_bot.plugins.builtin.commands import BuiltinCommandsPlugin
from nahida_bot.plugins.commands import CommandEntry, CommandRegistry
from nahida_bot.plugins.manifest import PluginManifest
from nahida_bot.scheduler.models import CronJob


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
        self.scheduler_service: Any | None = None

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

    def register_channel(self, channel: Any) -> None:
        pass

    def register_provider_type(
        self,
        type_key: str,
        factory: Any,
        *,
        config_schema: dict[str, Any] | None = None,
        description: str = "",
    ) -> None:
        pass

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
        # Mimic RealBotAPI: strip provider prefix from compound input
        bare_name = model_name
        if "/" in model_name:
            prefix, _, suffix = model_name.partition("/")
            if any(m["provider_id"] == prefix for m in self.models):
                bare_name = suffix
        if bare_name == "model-b":
            self.session_meta = {"provider_id": "p2", "model": bare_name}
            return "p2"
        return None

    async def get_session_info(self, session_id: str) -> dict[str, Any]:
        result = dict(self.session_meta)
        # Mimic RealBotAPI fallback: return default model info when empty
        if not result.get("model") and self.models:
            default = self.models[0]
            result.setdefault("provider_id", default["provider_id"])
            result.setdefault("model", default["model"])
        return result

    def list_commands(self) -> list[Any]:
        return [entry.to_info() for entry in self.command_registry.all_commands()]


@pytest.mark.asyncio
async def test_on_load_registers_commands_and_workspace_tools() -> None:
    api = _FakeAPI()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())

    await plugin.on_load()

    assert {"reset", "new", "status", "model", "help"} <= set(api.commands)
    assert {
        "workspace_read",
        "workspace_write",
        "memory_read",
        "memory_write",
        "cron_create",
        "cron_update",
        "cron_list",
        "cron_cancel",
        "cron_delete",
    } <= set(api.tools)
    assert api.tools["workspace_read"]["parameters"]["required"] == ["path"]
    assert api.tools["workspace_write"]["parameters"]["required"] == [
        "path",
        "content",
    ]
    assert api.tools["memory_read"]["parameters"]["required"] == []
    assert api.tools["memory_write"]["parameters"]["required"] == ["content"]
    create_params = api.tools["cron_create"]["parameters"]
    update_params = api.tools["cron_update"]["parameters"]
    assert create_params["properties"]["mode"]["enum"] == ["once", "interval", "cron"]
    assert "cron_expression" in create_params["properties"]
    assert update_params["properties"]["mode"]["enum"] == ["once", "interval", "cron"]
    assert "cron_expression" in update_params["properties"]


@pytest.mark.asyncio
async def test_workspace_tools_delegate_to_bot_api() -> None:
    api = _FakeAPI()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())

    result = await plugin._tool_workspace_write("notes/a.txt", "hello")
    assert result == "Written workspace file: notes/a.txt"
    assert await plugin._tool_workspace_read("notes/a.txt") == "hello"


@pytest.mark.asyncio
async def test_memory_tools_read_and_write_markdown_memory() -> None:
    api = _FakeAPI()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())

    result = await plugin._tool_memory_write(
        "User prefers Chinese architecture discussions.",
        target="both",
        section="Preferences",
    )

    assert "MEMORY.md" in result
    daily_paths = [path for path in api.files if path.startswith("memory/")]
    assert len(daily_paths) == 1
    assert "User prefers Chinese" in api.files["MEMORY.md"]
    assert "User prefers Chinese" in api.files[daily_paths[0]]

    read_result = await plugin._tool_memory_read(query="Chinese", days=1)
    assert "MEMORY.md" in read_result
    assert "User prefers Chinese" in read_result


@pytest.mark.asyncio
async def test_memory_write_rejects_secret_like_content() -> None:
    api = _FakeAPI()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())

    result = await plugin._tool_memory_write("api_key=secret-value")

    assert "secret" in result.lower()
    assert api.files == {}


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
    # Compound "provider_id/model" format should also work
    switched_compound = await plugin._cmd_model(
        args="p2/model-b", inbound=_inbound(), session_id="s1"
    )
    assert switched_compound == "Switched to p2/model-b (via p2)"
    missing = await plugin._cmd_model(
        args="missing", inbound=_inbound(), session_id="s1"
    )
    assert missing == "Model 'missing' not found in any provider."
    help_text = await plugin._cmd_help(args="", inbound=_inbound(), session_id="s1")
    assert "/help (h)" in help_text
    assert "Show help" in help_text


@pytest.mark.asyncio
async def test_model_and_status_show_default_for_new_session() -> None:
    """New sessions with empty metadata should show default model as current."""
    api = _FakeAPI()
    # session_meta is empty — simulates a brand-new session
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())

    # /model should mark the default model as (current)
    model_list = await plugin._cmd_model(args="", inbound=_inbound(), session_id="s1")
    assert "p1/model-a (current)" in model_list

    # /status should show the default model name, not "(default)"
    status = await plugin._cmd_status(args="", inbound=_inbound(), session_id="s1")
    assert "Provider: p1" in status
    assert "Model: model-a" in status
    assert "(default)" not in status


@pytest.mark.asyncio
async def test_new_command_switches_router_session() -> None:
    api = _FakeAPI()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())

    result = await plugin._cmd_new(args="", inbound=_inbound(), session_id="old")

    assert result == "New session started: telegram:c1:abc12345"
    assert api.new_sessions == [("telegram", "c1")]


def _cron_job(job_id: str = "job1", *, prompt: str = "old") -> CronJob:
    return CronJob(
        job_id=job_id,
        platform="telegram",
        chat_id="c1",
        session_key="telegram:c1",
        prompt=prompt,
        mode="interval",
        fire_at=None,
        interval_seconds=120,
        cron_expression=None,
        max_runs=None,
        run_count=0,
        is_active=True,
        created_at="2026-01-01T00:00:00+00:00",
        next_fire_at="2026-01-01T00:02:00+00:00",
        last_fired_at=None,
        workspace_id=None,
    )


class _FakeScheduler:
    def __init__(self) -> None:
        self.jobs = {"job1": _cron_job()}
        self.updated: dict[str, Any] = {}
        self.deleted: list[str] = []

    async def get_job(self, job_id: str) -> CronJob | None:
        return self.jobs.get(job_id)

    async def list_jobs(self, platform: str, chat_id: str) -> list[CronJob]:
        return [
            job
            for job in self.jobs.values()
            if job.platform == platform and job.chat_id == chat_id and job.is_active
        ]

    async def update_job(self, job_id: str, **kwargs: Any) -> CronJob:
        self.updated = {"job_id": job_id, **kwargs}
        job = _cron_job(job_id, prompt=kwargs.get("prompt") or "old")
        self.jobs[job_id] = job
        return job

    async def delete_job(self, job_id: str) -> bool:
        self.deleted.append(job_id)
        return self.jobs.pop(job_id, None) is not None


@pytest.mark.asyncio
async def test_cron_update_and_delete_tools_use_scheduler_api() -> None:
    api = _FakeAPI()
    api.scheduler_service = _FakeScheduler()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())
    token = current_session.set(
        SessionContext(platform="telegram", chat_id="c1", session_id="telegram:c1")
    )
    try:
        updated = await plugin._tool_cron_update(
            "job1",
            prompt="new prompt",
            interval_seconds=180,
            max_runs=3,
        )
        deleted = await plugin._tool_cron_delete("job1")
    finally:
        current_session.reset(token)

    assert "Updated task job1." in updated
    assert api.scheduler_service.updated == {
        "job_id": "job1",
        "prompt": "new prompt",
        "mode": None,
        "fire_at": None,
        "interval_seconds": 180,
        "cron_expression": None,
        "max_runs": 3,
    }
    assert deleted == "Deleted task job1."
    assert api.scheduler_service.deleted == ["job1"]
