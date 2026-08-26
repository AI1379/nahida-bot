"""Tests for the builtin commands and workspace tools plugin."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nahida_bot.agent.media.cache import MediaCache
from nahida_bot.agent.media.store import MediaStore
from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.context import SessionContext, current_session
from nahida_bot.core.runtime_settings import merge_runtime_meta
from nahida_bot.plugins.base import (
    ChatContext,
    InboundMessage,
    MemoryRef,
    OutboundMessage,
)
from nahida_bot.plugins.builtin.commands import BuiltinCommandsPlugin
from nahida_bot.plugins.commands import CommandEntry, CommandRegistry
from nahida_bot.plugins.manifest import PluginManifest
from nahida_bot.scheduler.models import CronJob


def _manifest(config: dict[str, Any] | None = None) -> PluginManifest:
    return PluginManifest(
        id="builtin-commands",
        name="Builtin Commands",
        version="0.1.0",
        entrypoint="nahida_bot.plugins.builtin.commands:BuiltinCommandsPlugin",
        config=config or {},
    )


def _inbound() -> InboundMessage:
    return InboundMessage(
        message_id="m1",
        platform="telegram",
        chat_id="c1",
        user_id="u1",
        text="/help",
        raw_event={},
        chat_context=ChatContext(
            platform="telegram",
            chat_type="private",
            platform_chat_id="c1",
        ),
    )


class _FakeAPI:
    def __init__(self) -> None:
        self.commands: dict[str, Any] = {}
        self.tools: dict[str, Any] = {}
        self.files: dict[str, str] = {}
        self.cleared: list[str] = []
        self.new_sessions: list[str] = []
        self.session_meta: dict[str, Any] = {}
        self.models = [
            {"provider_id": "p1", "model": "model-a"},
            {"provider_id": "p2", "model": "model-b"},
        ]
        self.command_registry = CommandRegistry()
        self.scheduler_service: Any | None = None
        self.stored_memories: list[tuple[str, str, dict[str, Any] | None]] = []
        self.updated_memories: list[tuple[str, str, str, dict[str, Any] | None]] = []
        self.archived_memories: list[str] = []
        self.workspace_root = Path("fake-workspace")
        self.sent_messages: list[tuple[str, OutboundMessage, str]] = []
        self.recorded_events: list[tuple[str, str, str, dict[str, Any] | None]] = []
        self.recorded_deliveries: list[dict[str, Any]] = []
        self.chat_history_rows: list[dict[str, Any]] = []
        self.chat_history_calls: list[dict[str, Any]] = []
        self.desktop_announcement_service: Any | None = None
        self.desktop_control_service: Any | None = None
        self.model_router: Any | None = None
        self.media_store: MediaStore | None = None
        self.image_fallback_model = ""

    def register_command(self, name: str, handler: Any, **kwargs: Any) -> None:
        self.commands[name] = (handler, kwargs)

    async def send_message(
        self, target: str, message: OutboundMessage, *, channel: str = ""
    ) -> str:
        self.sent_messages.append((target, message, channel))
        return "msg-1"

    async def record_session_event(
        self,
        session_id: str,
        content: str,
        *,
        source: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.recorded_events.append((session_id, content, source, metadata))

    async def record_message_delivery(self, **kwargs: Any) -> str:
        self.recorded_deliveries.append(kwargs)
        return "delivery-1"

    def on_event(self, event_type: type) -> Any:
        return lambda handler: handler

    def subscribe(self, event_type: type, handler: Any) -> Any:
        return None

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Any,
        *,
        requires_admin: bool = False,
        scope: str = "",
    ) -> None:
        self.tools[name] = {
            "description": description,
            "parameters": parameters,
            "handler": handler,
            "requires_admin": requires_admin,
            "scope": scope,
        }

    def get_model_router(self) -> Any | None:
        return self.model_router

    def get_media_store(self) -> MediaStore | None:
        return self.media_store

    def get_multimodal_image_fallback_model(self) -> str:
        return self.image_fallback_model

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

    def resolve_workspace_path(self, path: str) -> str:
        return str(self.workspace_root / path)

    def get_workspace_root(self, workspace_id: str | None = None) -> str | None:
        return str(self.workspace_root)

    async def memory_search(self, query: str, *, limit: int = 5) -> list[MemoryRef]:
        if query == "" or query == "nahida":
            return [
                MemoryRef(
                    key="mem_1",
                    content="Nahida prefers durable markdown memory.",
                    metadata={"title": "Preference"},
                )
            ][:limit]
        return []

    async def read_chat_history(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.chat_history_calls.append(kwargs)
        return list(self.chat_history_rows)

    async def memory_store(
        self, key: str, content: str, *, metadata: dict[str, Any] | None = None
    ) -> str:
        self.stored_memories.append((key, content, metadata))
        return f"mem_stored_{len(self.stored_memories)}"

    async def memory_update(
        self,
        item_id: str,
        content: str,
        *,
        key: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        self.updated_memories.append((item_id, content, key, metadata))
        return f"mem_updated_{len(self.updated_memories)}"

    async def memory_archive(self, item_id: str) -> bool:
        self.archived_memories.append(item_id)
        return True

    async def publish_event(self, event: Any) -> None:
        pass

    @property
    def logger(self) -> Any:
        return None

    async def clear_session(self, session_id: str) -> int:
        self.cleared.append(session_id)
        return 2

    async def start_new_session(self, address: ChatAddress) -> str | None:
        self.new_sessions.append(address.chat_key)
        return f"{address.chat_key}:abc12345"

    def get_session_run_status(self, session_id: str) -> dict[str, Any]:
        return {"active": False, "state": "idle", "pending_messages": 0}

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

    async def update_runtime_settings(
        self, session_id: str, updates: dict[str, Any]
    ) -> dict[str, Any]:
        existing = self.session_meta.get("runtime")
        runtime = merge_runtime_meta(
            existing if isinstance(existing, dict) else {},
            updates,
        )
        self.session_meta["runtime"] = runtime
        return runtime

    def list_commands(self) -> list[Any]:
        return [entry.to_info() for entry in self.command_registry.all_commands()]

    def register_status_provider(
        self, key: str, handler: Any, *, label: str = ""
    ) -> None:
        pass

    def unregister_status_provider(self, key: str) -> bool:
        return False

    async def collect_status_providers(
        self, *, session_id: str, chat_key: str
    ) -> list[str]:
        return []


@pytest.mark.asyncio
async def test_on_load_registers_commands_and_workspace_tools() -> None:
    api = _FakeAPI()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())

    await plugin.on_load()

    assert {
        "reset",
        "new",
        "status",
        "model",
        "reasoning",
        "help",
        "memory",
        "identity",
    } <= set(api.commands)
    assert {
        "message",
        "workspace_read",
        "workspace_write",
        "send_local_attachment",
        "memory_read",
        "memory_write",
        "memory_update",
        "memory_archive",
        "read_chat_history",
        "search_chat_history",
        "find_chat",
        "plan",
        "cron_create",
        "cron_update",
        "cron_list",
        "cron_cancel",
        "cron_delete",
        "desktop_announce",
        "desktop_exec",
        "desktop_file_read",
        "desktop_screenshot_capture",
        "desktop_screen_observe",
        "desktop_screenshot_send",
        "desktop_input",
    } <= set(api.tools)
    assert api.tools["workspace_read"]["parameters"]["required"] == ["path"]
    assert api.tools["workspace_write"]["parameters"]["required"] == [
        "path",
        "content",
    ]
    message_params = api.tools["message"]["parameters"]
    assert api.tools["send_local_attachment"]["parameters"]["required"] == ["path"]
    assert message_params["required"] == ["target", "text"]
    assert api.tools["memory_read"]["parameters"]["required"] == []
    assert api.tools["memory_write"]["parameters"]["required"] == ["content"]
    assert api.tools["read_chat_history"]["parameters"]["required"] == ["mode"]
    assert api.tools["plan"]["parameters"]["required"] == ["action"]
    for tool_name in {
        "exec",
        "message",
        "workspace_write",
        "desktop_exec",
        "desktop_file_read",
        "desktop_screenshot_capture",
        "desktop_screen_observe",
        "desktop_screenshot_send",
        "desktop_input",
        "identity_manage",
    }:
        assert api.tools[tool_name]["requires_admin"] is True
    # History tools are chat-domain scoped instead of admin-only: non-admin
    # senders reach the current chat plus its declared sibling groups.
    for tool_name in {
        "read_chat_history",
        "search_chat_history",
        "find_chat",
        "recall_cross_chat",
    }:
        assert api.tools[tool_name]["scope"] == "chat_domain"
        assert api.tools[tool_name]["requires_admin"] is False
    assert api.tools["workspace_read"]["requires_admin"] is False
    assert api.tools["memory_write"]["requires_admin"] is False
    create_params = api.tools["cron_create"]["parameters"]
    update_params = api.tools["cron_update"]["parameters"]
    assert create_params["properties"]["mode"]["enum"] == ["once", "interval", "cron"]
    assert create_params["properties"]["session_mode"]["enum"] == [
        "main",
        "isolated",
        "fresh",
    ]
    assert "cron_expression" in create_params["properties"]
    assert update_params["properties"]["mode"]["enum"] == ["once", "interval", "cron"]
    assert "cron_expression" in update_params["properties"]
    assert set(api.tools["desktop_exec"]["parameters"]["properties"]) == {
        "program",
        "args",
        "cwd",
    }
    assert set(api.tools["desktop_file_read"]["parameters"]["properties"]) == {
        "path",
        "root_id",
        "offset",
        "max_bytes",
    }
    exec_params = api.tools["desktop_exec"]["parameters"]
    read_params = api.tools["desktop_file_read"]["parameters"]
    observe_params = api.tools["desktop_screen_observe"]["parameters"]
    send_params = api.tools["desktop_screenshot_send"]["parameters"]
    input_params = api.tools["desktop_input"]["parameters"]
    assert exec_params["required"] == ["program"]
    assert exec_params["properties"]["args"]["default"] == []
    assert exec_params["properties"]["cwd"]["default"] == ""
    assert read_params["required"] == ["path"]
    assert read_params["properties"]["root_id"]["default"] == ""
    assert read_params["properties"]["offset"]["default"] == 0
    assert read_params["properties"]["max_bytes"]["default"] == 65536
    assert observe_params["properties"]["question"]["default"]
    assert observe_params["properties"]["media_id"]["default"] == ""
    assert send_params["properties"]["attachment_type"]["enum"] == [
        "photo",
        "document",
    ]
    assert input_params["required"] == ["action"]
    assert input_params["properties"]["x"]["maximum"] == 1000
    assert input_params["properties"]["action"]["enum"] == [
        "click",
        "key",
        "move",
        "scroll",
        "type",
    ]
    for parameters in (exec_params, read_params):
        assert parameters["additionalProperties"] is False
        assert {
            "actor",
            "actor_account_key",
            "node",
            "node_id",
            "capability",
        }.isdisjoint(parameters["properties"])


@pytest.mark.asyncio
async def test_desktop_announce_uses_trusted_cron_context() -> None:
    api = _FakeAPI()
    calls: list[dict[str, Any]] = []

    class _AnnouncementService:
        async def announce(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            return SimpleNamespace(ok=True, node_id="desktop-owner")

    api.desktop_announcement_service = _AnnouncementService()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())
    token = current_session.set(
        SessionContext(
            platform="milky",
            chat_id="owner",
            session_id="milky:private:owner:cron:job-1",
            conversation_id="milky:private:owner",
            sender_account_key="milky:user:owner",
            origin="cron_trigger",
        )
    )
    try:
        result = await plugin._tool_desktop_announce("该休息一下了。")
    finally:
        current_session.reset(token)

    assert result == "Desktop announcement queued on desktop-owner."
    assert calls == [
        {
            "message": "该休息一下了。",
            "conversation_id": "milky:private:owner",
            "actor_account_key": "milky:user:owner",
            "caller": "agent:cron:milky:private:owner:cron:job-1",
        }
    ]


@pytest.mark.asyncio
async def test_desktop_announce_rejects_non_cron_context() -> None:
    api = _FakeAPI()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())
    token = current_session.set(
        SessionContext(
            platform="milky",
            chat_id="owner",
            session_id="milky:private:owner",
        )
    )
    try:
        result = await plugin._tool_desktop_announce("unexpected")
    finally:
        current_session.reset(token)

    assert result == "Error: desktop_announce is only available during CRON runs."


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["", "cron_trigger"])
async def test_desktop_exec_uses_trusted_context_in_chat_and_cron(origin: str) -> None:
    api = _FakeAPI()
    calls: list[dict[str, Any]] = []

    class _ControlService:
        async def exec(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            return SimpleNamespace(ok=True, payload={"stdout": "ok", "exit_code": 0})

    api.desktop_control_service = _ControlService()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())
    token = current_session.set(
        SessionContext(
            platform="milky",
            chat_id="owner",
            session_id="milky:private:owner",
            conversation_id="milky:private:owner",
            sender_account_key="milky:user:owner",
            origin=origin,
        )
    )
    try:
        result = await plugin._desktop_tools.exec("git", ["status"], "repo")
    finally:
        current_session.reset(token)

    assert '"ok": true' in result
    assert calls == [
        {
            "program": "git",
            "args": ["status"],
            "cwd": "repo",
            "conversation_id": "milky:private:owner",
            "actor_account_key": "milky:user:owner",
            "caller": f"agent:{origin or 'chat'}:milky:private:owner",
        }
    ]


@pytest.mark.asyncio
async def test_desktop_control_tool_rejects_missing_actor() -> None:
    api = _FakeAPI()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())
    token = current_session.set(
        SessionContext(
            platform="milky", chat_id="owner", session_id="milky:private:owner"
        )
    )
    try:
        result = await plugin._desktop_tools.file_read("a.txt", "docs", 0, 100)
    finally:
        current_session.reset(token)

    assert '"code": "actor_unavailable"' in result


@pytest.mark.asyncio
async def test_desktop_input_uses_trusted_context() -> None:
    api = _FakeAPI()
    calls: list[dict[str, Any]] = []

    class _ControlService:
        async def input(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            return SimpleNamespace(ok=True, payload={"applied": True})

    api.desktop_control_service = _ControlService()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())
    token = current_session.set(
        SessionContext(
            platform="milky",
            chat_id="owner",
            session_id="milky:private:owner",
            conversation_id="milky:private:owner",
            sender_account_key="milky:user:owner",
        )
    )
    try:
        result = await plugin._desktop_tools.input("click", x=500, y=250)
    finally:
        current_session.reset(token)

    assert '"ok": true' in result
    assert calls == [
        {
            "action": "click",
            "x": 500,
            "y": 250,
            "button": "left",
            "clicks": 1,
            "scroll_steps": 0,
            "text": "",
            "keys": [],
            "conversation_id": "milky:private:owner",
            "actor_account_key": "milky:user:owner",
            "caller": "agent:chat:milky:private:owner",
        }
    ]


@pytest.mark.asyncio
async def test_desktop_screen_observe_sends_pixels_only_to_vision_model(
    tmp_path: Path,
) -> None:
    api = _FakeAPI()
    captured_messages: list[Any] = []
    route_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    class _ControlService:
        async def screenshot(self, **kwargs: Any) -> Any:
            return SimpleNamespace(
                ok=True,
                payload={
                    "mimeType": "image/jpeg",
                    "data": "aGVsbG8=",
                    "imageWidth": 800,
                    "imageHeight": 450,
                    "capturedAtMs": 123,
                    "coordinateSpace": {
                        "type": "normalized",
                        "minimum": 0,
                        "maximum": 1000,
                    },
                },
            )

    class _Provider:
        async def chat(self, **kwargs: Any) -> Any:
            captured_messages.extend(kwargs["messages"])
            return SimpleNamespace(content="Settings button at x=900, y=80.")

    class _Router:
        def resolve_for_task(self, *args: Any, **kwargs: Any) -> Any:
            route_calls.append((args, kwargs))
            return SimpleNamespace(
                slot=SimpleNamespace(
                    id="vision-provider",
                    default_model="vision-model",
                    provider=_Provider(),
                ),
                model="vision-model",
            )

    api.desktop_control_service = _ControlService()
    api.model_router = _Router()
    api.media_store = MediaStore(MediaCache(tmp_path / "media", ttl_seconds=90))
    api.image_fallback_model = "configured-vision/model"
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())
    token = current_session.set(
        SessionContext(
            platform="milky",
            chat_id="owner",
            session_id="milky:private:owner",
            conversation_id="milky:private:owner",
            sender_account_key="milky:user:owner",
        )
    )
    try:
        result = await plugin._desktop_tools.screen_observe("Locate Settings")
    finally:
        current_session.reset(token)

    assert "Settings button" in result
    assert "aGVsbG8=" not in result
    assert len(captured_messages) == 1
    assert [part.type for part in captured_messages[0].parts] == [
        "text",
        "image_base64",
    ]
    assert captured_messages[0].parts[1].data == "aGVsbG8="
    assert route_calls == [
        (
            ("desktop_screen_observe",),
            {
                "explicit": "configured-vision/model",
                "default_spec": "vision",
                "fallback": "disabled",
            },
        )
    ]
    assert '"mediaId": "desktop-screenshot:' in result
    assert '"expiresInSeconds": 90' in result


@pytest.mark.asyncio
async def test_desktop_screenshot_capture_and_send_reuse_actor_bound_media(
    tmp_path: Path,
) -> None:
    api = _FakeAPI()
    screenshot_calls = 0

    class _ControlService:
        async def screenshot(self, **kwargs: Any) -> Any:
            nonlocal screenshot_calls
            screenshot_calls += 1
            return SimpleNamespace(
                ok=True,
                payload={
                    "mimeType": "image/png",
                    "data": "c2NyZWVuLXBpeGVscw==",
                    "imageWidth": 1920,
                    "imageHeight": 1080,
                    "capturedAtMs": 456,
                    "coordinateSpace": {
                        "type": "normalized",
                        "minimum": 0,
                        "maximum": 1000,
                    },
                },
            )

    api.desktop_control_service = _ControlService()
    api.media_store = MediaStore(MediaCache(tmp_path / "media", ttl_seconds=120))
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())
    token = current_session.set(
        SessionContext(
            platform="milky",
            chat_id="owner",
            session_id="milky:private:owner",
            chat_address=ChatAddress.parse("milky:private:owner"),
            sender_account_key="milky:user:owner",
        )
    )
    try:
        captured = json.loads(await plugin._desktop_tools.screenshot_capture())
        media_id = captured["media"]["mediaId"]
        sent = json.loads(
            await plugin._desktop_tools.screenshot_send(
                media_id=media_id,
                caption="当前桌面",
                attachment_type="document",
            )
        )
    finally:
        current_session.reset(token)

    assert screenshot_calls == 1
    assert captured["ok"] is True
    assert captured["media"]["expiresInSeconds"] == 120
    assert "path" not in captured["media"]
    assert "data" not in captured["media"]
    assert sent["messageId"] == "msg-1"
    assert len(api.sent_messages) == 1
    target, message, channel = api.sent_messages[0]
    assert (target, channel) == ("owner", "milky")
    assert message.extra == {"chat_address": "milky:private:owner"}
    assert len(message.attachments) == 1
    attachment = message.attachments[0]
    assert attachment.type == "document"
    assert attachment.mime_type == "image/png"
    assert attachment.caption == "当前桌面"
    assert Path(attachment.path).read_bytes() == b"screen-pixels"


@pytest.mark.asyncio
async def test_desktop_screenshot_media_id_is_actor_bound(tmp_path: Path) -> None:
    api = _FakeAPI()
    api.desktop_control_service = SimpleNamespace()
    api.media_store = MediaStore(MediaCache(tmp_path / "media"))
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())
    token = current_session.set(
        SessionContext(
            platform="milky",
            chat_id="other",
            session_id="milky:private:other",
            sender_account_key="milky:user:other",
        )
    )
    try:
        result = await plugin._desktop_tools.screenshot_send(
            media_id="desktop-screenshot:not-this-actor:abc"
        )
    finally:
        current_session.reset(token)

    assert '"code": "media_forbidden"' in result
    assert api.sent_messages == []


@pytest.mark.asyncio
async def test_read_chat_history_tool_formats_structured_provenance() -> None:
    api = _FakeAPI()
    api.chat_history_rows = [
        {
            "turn_id": 41,
            "session_id": "milky:group:1",
            "role": "user",
            "source": "group_observation",
            "content": "the deployment finished",
            "created_at": "2026-07-10T10:00:00+00:00",
            "message_id": "m41",
            "reply_to": "m39",
            "sender_id": "u1",
            "sender_display_name": "Alice",
            "observed_only": True,
            "trigger_kind": "observed",
        }
    ]
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())

    result = await plugin._history_tools.read(
        mode="around_message",
        chat_address="milky:group:1",
        message_id="m41",
        before=3,
        after=4,
    )

    assert "chronological" in result
    assert "[Alice]" in result
    assert "message_id=m41" in result
    assert "reply_to=m39" in result
    assert "the deployment finished" in result
    assert api.chat_history_calls[0]["mode"] == "around_message"
    assert api.chat_history_calls[0]["before"] == 3
    assert api.chat_history_calls[0]["after"] == 4


@pytest.mark.asyncio
async def test_read_chat_history_tool_validates_mode_inputs() -> None:
    plugin = BuiltinCommandsPlugin(api=_FakeAPI(), manifest=_manifest())

    assert "requires message_id" in await plugin._history_tools.read(
        mode="around_message"
    )
    assert "requires query" in await plugin._history_tools.read(mode="search")
    assert "requires since" in await plugin._history_tools.read(mode="time_range")


@pytest.mark.asyncio
async def test_message_tool_notify_records_delivery_audit_only() -> None:
    api = _FakeAPI()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())
    token = current_session.set(
        SessionContext(
            platform="telegram",
            chat_id="c1",
            session_id="telegram:private:c1",
            chat_address=ChatAddress(
                channel="telegram", target_type="private", target_id="c1"
            ),
            user_id="u1",
        )
    )
    try:
        result = await plugin._message_tools.send(
            text="hello",
            target="telegram:private:c2",
            delivery="notify",
        )
    finally:
        current_session.reset(token)

    assert "Message sent to telegram:private:c2" in result
    assert api.sent_messages[0][0] == "c2"
    assert api.sent_messages[0][1].extra["chat_address"] == "telegram:private:c2"
    assert api.recorded_events == []
    assert len(api.recorded_deliveries) == 1
    delivery = api.recorded_deliveries[0]
    assert delivery["target"].chat_key == "telegram:private:c2"
    assert delivery["source"] == "message_tool"
    assert delivery["delivery_mode"] == "notify"
    assert delivery["metadata"]["from_user_id"] == "u1"


@pytest.mark.asyncio
async def test_message_tool_record_keeps_cross_session_turn_and_audit() -> None:
    api = _FakeAPI()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())
    token = current_session.set(
        SessionContext(
            platform="telegram",
            chat_id="c1",
            session_id="telegram:private:c1",
            chat_address=ChatAddress(
                channel="telegram", target_type="private", target_id="c1"
            ),
            user_id="u1",
        )
    )
    try:
        result = await plugin._message_tools.send(
            text="hello",
            target="telegram:private:c2",
            delivery="record",
        )
    finally:
        current_session.reset(token)

    assert "recorded in target session history" in result
    assert len(api.recorded_deliveries) == 1
    assert api.recorded_deliveries[0]["delivery_mode"] == "record"
    assert api.recorded_events == [
        (
            "telegram:private:c2",
            "hello",
            "cross_session_message",
            {
                "from_session": "telegram:private:c1",
                "from_platform": "telegram",
                "from_chat_id": "c1",
                "from_user_id": "u1",
                "from_chat_address": "telegram:private:c1",
            },
        )
    ]


@pytest.mark.asyncio
async def test_workspace_tools_delegate_to_bot_api() -> None:
    api = _FakeAPI()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())

    result = await plugin._workspace_tools.write("notes/a.txt", "hello")
    assert result == "Written workspace file: notes/a.txt"
    assert await plugin._workspace_tools.read("notes/a.txt") == "hello"


@pytest.mark.asyncio
async def test_plan_tool_manages_durable_task_state() -> None:
    api = _FakeAPI()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())

    created = await plugin._plan_tools.execute(
        action="create",
        title="Refactor",
        tasks=[
            {"title": "Extract tools", "detail": "Keep compatibility wrappers"},
            {"title": "Run tests"},
        ],
    )
    updated = await plugin._plan_tools.execute(
        action="update",
        task_id=1,
        status="completed",
        detail="Done",
    )
    added = await plugin._plan_tools.execute(
        action="add", tasks=[{"title": "Review quality"}]
    )
    removed = await plugin._plan_tools.execute(action="remove", task_id=2)

    assert "Plan: Refactor" in created
    assert "1. [x] Extract tools — Done" in updated
    assert "3. [ ] Review quality" in added
    assert "2. [ ] Review quality" in removed
    assert await plugin._plan_tools.execute(action="list") == removed.removeprefix(
        "Task removed.\n"
    )


@pytest.mark.asyncio
async def test_plan_tool_validates_updates_and_clear() -> None:
    api = _FakeAPI()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())

    assert await plugin._plan_tools.execute(action="list") == (
        "No plan exists. Use action='create' to start one."
    )
    await plugin._plan_tools.execute(action="create", tasks=[{"title": "One"}])

    assert await plugin._plan_tools.execute(
        action="update", task_id=1, status="unknown"
    ) == (
        "Error: Invalid status 'unknown'. Must be one of: "
        "completed, failed, in_progress, pending"
    )
    assert await plugin._plan_tools.execute(action="remove") == (
        "Error: task_id is required for remove."
    )
    assert await plugin._plan_tools.execute(action="clear") == "Plan cleared."
    assert await plugin._plan_tools.execute(action="list") == (
        "No plan exists. Use action='create' to start one."
    )


class _FakeOrchestrator:
    def __init__(self) -> None:
        self.spec: Any = None
        self.wait_timeout: int | None = None
        self.list_request: tuple[str, int] | None = None
        self.stop_request: tuple[str, str] | None = None
        self.task = SimpleNamespace(
            task_id="task-1",
            child_session_id="agent:task-1",
            requester_session_id="telegram:private:c1",
            status=SimpleNamespace(value="running"),
            title="Review",
            summary="",
            error="",
        )

    async def spawn_subagent(self, spec: Any) -> Any:
        self.spec = spec
        return self.task

    async def wait_for_task(self, task_id: str, *, timeout_seconds: int) -> Any:
        self.wait_timeout = timeout_seconds
        return self.task if task_id == self.task.task_id else None

    async def list_tasks(self, requester_session_id: str, *, limit: int) -> list[Any]:
        self.list_request = (requester_session_id, limit)
        return [self.task]

    async def stop_task(self, requester_session_id: str, task_id: str) -> Any:
        self.stop_request = (requester_session_id, task_id)
        return self.task


@pytest.mark.asyncio
async def test_agent_tools_delegate_with_session_ownership() -> None:
    api = _FakeAPI()
    orchestrator = _FakeOrchestrator()
    api.orchestration_service = orchestrator
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())
    token = current_session.set(
        SessionContext(
            platform="telegram",
            chat_id="c1",
            session_id="telegram:private:c1",
        )
    )
    try:
        spawned = await plugin._agent_tools.spawn(
            "Review the refactor",
            label="Review",
            context_mode="summary",
            handoff_summary="Plan and cron are extracted",
            tool_allowlist=["workspace_read"],
        )
        waited = await plugin._agent_tools.wait("task-1", timeout_seconds=-1)
        listed = await plugin._agent_tools.list_tasks(limit=0)
        stopped = await plugin._agent_tools.stop("task-1")
    finally:
        current_session.reset(token)

    assert '"task_id": "task-1"' in spawned
    assert orchestrator.spec.task == "Review the refactor"
    assert orchestrator.spec.context_mode == "summary"
    assert orchestrator.spec.tool_allowlist == ("workspace_read",)
    assert "task-1: running — Review" in waited
    assert listed == waited
    assert stopped == waited
    assert orchestrator.wait_timeout == 0
    assert orchestrator.list_request == ("telegram:private:c1", 1)
    assert orchestrator.stop_request == ("telegram:private:c1", "task-1")


@pytest.mark.asyncio
async def test_agent_wait_hides_tasks_owned_by_another_session() -> None:
    api = _FakeAPI()
    orchestrator = _FakeOrchestrator()
    orchestrator.task.requester_session_id = "telegram:private:other"
    api.orchestration_service = orchestrator
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())
    token = current_session.set(
        SessionContext(
            platform="telegram",
            chat_id="c1",
            session_id="telegram:private:c1",
        )
    )
    try:
        result = await plugin._agent_tools.wait("task-1")
    finally:
        current_session.reset(token)

    assert result == "Task task-1 not found."


@pytest.mark.asyncio
async def test_send_local_attachment_sends_in_current_session(tmp_path: Path) -> None:
    api = _FakeAPI()
    api.workspace_root = tmp_path
    image_path = tmp_path / "images" / "a.png"
    image_path.parent.mkdir()
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())
    token = current_session.set(
        SessionContext(
            platform="telegram",
            chat_id="c1",
            session_id="telegram:private:c1",
            chat_address=ChatAddress(
                channel="telegram", target_type="private", target_id="c1"
            ),
        )
    )
    try:
        result = await plugin._message_tools.send_local_attachment(
            "images/a.png", caption="caption"
        )
    finally:
        current_session.reset(token)

    assert result == "Attachment sent: msg-1"
    assert len(api.sent_messages) == 1
    target, outbound, channel = api.sent_messages[0]
    assert target == "c1"
    assert channel == "telegram"
    assert outbound.text == ""
    assert outbound.extra["chat_address"] == "telegram:private:c1"
    assert len(outbound.attachments) == 1
    attachment = outbound.attachments[0]
    assert attachment.type == "photo"
    assert attachment.path == str(image_path)
    assert attachment.filename == "a.png"
    assert attachment.caption == "caption"


@pytest.mark.asyncio
async def test_send_local_attachment_supports_document_type(tmp_path: Path) -> None:
    api = _FakeAPI()
    api.workspace_root = tmp_path
    doc_path = tmp_path / "report.bin"
    doc_path.write_bytes(b"data")
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())
    token = current_session.set(
        SessionContext(
            platform="milky",
            chat_id="20001",
            session_id="milky:group:20001",
            chat_address=ChatAddress(
                channel="milky", target_type="group", target_id="20001"
            ),
        )
    )
    try:
        result = await plugin._message_tools.send_local_attachment(
            "report.bin", attachment_type="document", filename="report.dat"
        )
    finally:
        current_session.reset(token)

    assert result == "Attachment sent: msg-1"
    target, outbound, channel = api.sent_messages[0]
    assert target == "20001"
    assert channel == "milky"
    assert outbound.attachments[0].type == "document"
    assert outbound.attachments[0].filename == "report.dat"
    assert outbound.extra["chat_address"] == "milky:group:20001"


@pytest.mark.asyncio
async def test_send_local_attachment_requires_session_context(tmp_path: Path) -> None:
    api = _FakeAPI()
    api.workspace_root = tmp_path
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())

    result = await plugin._message_tools.send_local_attachment("a.png")

    assert result == "Error: No active session context."
    assert api.sent_messages == []


@pytest.mark.asyncio
async def test_send_local_attachment_rejects_missing_file(tmp_path: Path) -> None:
    api = _FakeAPI()
    api.workspace_root = tmp_path
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())
    token = current_session.set(
        SessionContext(
            platform="telegram",
            chat_id="c1",
            session_id="telegram:private:c1",
            chat_address=ChatAddress(
                channel="telegram", target_type="private", target_id="c1"
            ),
        )
    )
    try:
        result = await plugin._message_tools.send_local_attachment("missing.png")
    finally:
        current_session.reset(token)

    assert result == "Error: File does not exist: missing.png"
    assert api.sent_messages == []


@pytest.mark.asyncio
async def test_send_local_attachment_rejects_absolute_path_by_default(
    tmp_path: Path,
) -> None:
    api = _FakeAPI()
    file_path = tmp_path / "a.png"
    file_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())
    token = current_session.set(
        SessionContext(
            platform="telegram",
            chat_id="c1",
            session_id="telegram:private:c1",
            chat_address=ChatAddress(
                channel="telegram", target_type="private", target_id="c1"
            ),
        )
    )
    try:
        result = await plugin._message_tools.send_local_attachment(str(file_path))
    finally:
        current_session.reset(token)

    assert "Absolute attachment paths are disabled" in result
    assert api.sent_messages == []


@pytest.mark.asyncio
async def test_send_local_attachment_allows_absolute_path_when_configured(
    tmp_path: Path,
) -> None:
    api = _FakeAPI()
    file_path = tmp_path / "a.png"
    file_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    plugin = BuiltinCommandsPlugin(
        api=api,
        manifest=_manifest({"allow_external_attachment_paths": True}),
    )
    token = current_session.set(
        SessionContext(
            platform="telegram",
            chat_id="c1",
            session_id="telegram:private:c1",
            chat_address=ChatAddress(
                channel="telegram", target_type="private", target_id="c1"
            ),
        )
    )
    try:
        result = await plugin._message_tools.send_local_attachment(str(file_path))
    finally:
        current_session.reset(token)

    assert result == "Attachment sent: msg-1"
    assert api.sent_messages[0][1].attachments[0].path == str(file_path)


@pytest.mark.asyncio
async def test_send_local_attachment_enforces_external_root_allowlist(
    tmp_path: Path,
) -> None:
    api = _FakeAPI()
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    allowed_file = allowed / "a.png"
    outside_file = outside / "b.png"
    allowed_file.write_bytes(b"\x89PNG\r\n\x1a\n")
    outside_file.write_bytes(b"\x89PNG\r\n\x1a\n")
    plugin = BuiltinCommandsPlugin(
        api=api,
        manifest=_manifest(
            {
                "allow_external_attachment_paths": True,
                "external_attachment_roots": [str(allowed)],
            }
        ),
    )
    token = current_session.set(
        SessionContext(
            platform="telegram",
            chat_id="c1",
            session_id="telegram:private:c1",
            chat_address=ChatAddress(
                channel="telegram", target_type="private", target_id="c1"
            ),
        )
    )
    try:
        rejected = await plugin._message_tools.send_local_attachment(str(outside_file))
        accepted = await plugin._message_tools.send_local_attachment(str(allowed_file))
    finally:
        current_session.reset(token)

    assert "outside allowed external roots" in rejected
    assert accepted == "Attachment sent: msg-1"
    assert len(api.sent_messages) == 1
    assert api.sent_messages[0][1].attachments[0].path == str(allowed_file)


@pytest.mark.asyncio
async def test_memory_tools_use_structured_store() -> None:
    api = _FakeAPI()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())

    result = await plugin._memory_tools.write(
        "User prefers Chinese architecture discussions.",
        title="Language preference",
        kind="preference",
    )

    assert "mem_stored_1" in result
    assert api.files == {}
    key, content, metadata = api.stored_memories[-1]
    assert key == "Language preference"
    assert content == "User prefers Chinese architecture discussions."
    assert metadata is not None
    assert metadata["kind"] == "preference"
    assert metadata["audience"] == "current"
    assert metadata["portable"] is True

    read_result = await plugin._memory_tools.read(query="nahida")
    assert "Memory results:" in read_result
    assert "mem_1" in read_result


@pytest.mark.asyncio
async def test_memory_write_can_mark_current_chat_context_non_portable() -> None:
    api = _FakeAPI()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())

    result = await plugin._memory_tools.write(
        "People in this group call the current sender Old Wang.",
        title="Group-local alias",
        audience="global",
        portable=False,
    )

    assert "mem_stored_1" in result
    metadata = api.stored_memories[-1][2]
    assert metadata is not None
    assert metadata["portable"] is False
    assert metadata["audience"] == "current"


@pytest.mark.asyncio
async def test_memory_write_rejects_secret_like_content() -> None:
    api = _FakeAPI()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())

    result = await plugin._memory_tools.write("api_key=secret-value")

    assert "secret" in result.lower()
    assert api.files == {}


@pytest.mark.asyncio
async def test_memory_write_private_skips_markdown_and_persists_structured() -> None:
    """A private/secret_like write must not leak via Markdown (review #1).

    Workspace Markdown is auto-injected every turn with no sensitivity filter,
    so sensitive writes route SOLELY to the structured durable store.
    """
    api = _FakeAPI()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())

    result = await plugin._memory_tools.write(
        "this stays between us",
        title="Private",
        sensitivity="private",
    )

    # No Markdown written -> cannot leak via per-turn auto-injection.
    assert api.files == {}
    # Routed to the structured store with the explicit sensitivity tag.
    assert api.stored_memories
    _key, content, metadata = api.stored_memories[-1]
    assert content == "this stays between us"
    assert metadata is not None and metadata.get("sensitivity") == "private"
    assert "mem_stored_1" in result


@pytest.mark.asyncio
async def test_memory_update_and_archive_tools_use_item_ids() -> None:
    api = _FakeAPI()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())

    updated = await plugin._memory_tools.update(
        item_id="mem_1",
        content="Corrected durable content.",
        title="Corrected",
        kind="fact",
        portable=False,
    )
    archived = await plugin._memory_tools.archive("mem_2", "duplicate")

    assert "mem_updated_1" in updated
    assert api.updated_memories[0][0] == "mem_1"
    assert api.updated_memories[0][2] == "Corrected"
    update_metadata = api.updated_memories[0][3]
    assert update_metadata is not None
    assert update_metadata["portable"] is False
    assert archived == "Memory archived: mem_2"
    assert api.archived_memories == ["mem_2"]


@pytest.mark.asyncio
async def test_memory_update_blocks_reassign_without_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NAHIDA_MEMORY_REASSIGN", raising=False)
    api = _FakeAPI()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())

    result = await plugin._memory_tools.update(
        item_id="mem_1",
        content="reassign content",
        target_scope_type="person",
        target_scope_id="owner",
    )
    assert "NAHIDA_MEMORY_REASSIGN" in result


@pytest.mark.asyncio
async def test_memory_update_allows_reassign_with_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NAHIDA_MEMORY_REASSIGN", "1")
    api = _FakeAPI()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())

    result = await plugin._memory_tools.update(
        item_id="mem_1",
        content="reassign content",
        target_scope_type="person",
        target_scope_id="owner",
    )
    assert "mem_updated_1" in result
    _item_id, _content, _key, metadata = api.updated_memories[0]
    assert metadata is not None
    assert metadata["target_scope_type"] == "person"
    assert metadata["target_scope_id"] == "owner"


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
    assert "Reasoning display: default" in status
    assert "Reasoning effort: default" in status
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
async def test_reasoning_command_updates_runtime_settings() -> None:
    api = _FakeAPI()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())

    initial = await plugin._cmd_reasoning(args="", inbound=_inbound(), session_id="s1")
    assert "display: default" in initial
    assert "effort: default" in initial

    enabled = await plugin._cmd_reasoning(
        args="on", inbound=_inbound(), session_id="s1"
    )
    assert "display: on" in enabled
    assert api.session_meta["runtime"]["reasoning"]["show"] is True

    effort = await plugin._cmd_reasoning(
        args="effort high", inbound=_inbound(), session_id="s1"
    )
    assert "effort: high" in effort
    assert api.session_meta["runtime"]["reasoning"]["effort"] == "high"

    reset_effort = await plugin._cmd_reasoning(
        args="effort default", inbound=_inbound(), session_id="s1"
    )
    assert "effort: default" in reset_effort
    assert "effort" not in api.session_meta["runtime"]["reasoning"]

    reset_all = await plugin._cmd_reasoning(
        args="reset", inbound=_inbound(), session_id="s1"
    )
    assert "display: default" in reset_all
    assert api.session_meta["runtime"] == {}


@pytest.mark.asyncio
async def test_memory_command_search_list_and_remember() -> None:
    api = _FakeAPI()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())

    search_result = await plugin._cmd_memory(
        args="search nahida", inbound=_inbound(), session_id="s1"
    )
    assert "Nahida prefers durable" in search_result

    list_result = await plugin._cmd_memory(
        args="list", inbound=_inbound(), session_id="s1"
    )
    assert "mem_1" in list_result

    remember_result = await plugin._cmd_memory(
        args="remember User prefers Chinese search.",
        inbound=_inbound(),
        session_id="s1",
    )
    assert remember_result == "Memory stored."
    assert api.stored_memories[0][1] == "User prefers Chinese search."


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
async def test_status_shows_session_key_kind() -> None:
    api = _FakeAPI()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())

    typed = await plugin._cmd_status(
        args="", inbound=_inbound(), session_id="telegram:private:c1"
    )
    legacy = await plugin._cmd_status(
        args="", inbound=_inbound(), session_id="telegram:c1:abc12345"
    )

    assert "Session key: typed" in typed
    assert "Session key: legacy derived" in legacy


@pytest.mark.asyncio
async def test_new_command_switches_router_session() -> None:
    api = _FakeAPI()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())

    result = await plugin._cmd_new(args="", inbound=_inbound(), session_id="old")

    assert result == "New session started: telegram:private:c1:abc12345"
    assert api.new_sessions == ["telegram:private:c1"]


def _cron_job(
    job_id: str = "job1",
    *,
    prompt: str = "old",
    created_by_user_id: str = "",
) -> CronJob:
    return CronJob(
        job_id=job_id,
        platform="telegram",
        chat_id="c1",
        session_key="telegram:private:c1",
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
        chat_type="private",
        created_by_user_id=created_by_user_id,
        created_from_session_id="telegram:private:c1",
        created_from_chat_address="telegram:private:c1",
    )


class _FakeScheduler:
    def __init__(self) -> None:
        self.jobs = {"job1": _cron_job()}
        self.updated: dict[str, Any] = {}
        self.deleted: list[str] = []
        self.created: dict[str, Any] = {}

    async def create_job(self, **kwargs: Any) -> CronJob:
        self.created = dict(kwargs)
        job = _cron_job("job-created", prompt=kwargs["prompt"])
        self.jobs[job.job_id] = job
        return job

    async def get_job(self, job_id: str) -> CronJob | None:
        return self.jobs.get(job_id)

    async def list_jobs(self, address: ChatAddress) -> list[CronJob]:
        return [
            job
            for job in self.jobs.values()
            if job.platform == address.channel
            and job.chat_id == address.target_id
            and job.chat_type == address.target_type
            and job.is_active
        ]

    async def update_job(self, job_id: str, **kwargs: Any) -> CronJob:
        self.updated = {"job_id": job_id, **kwargs}
        job = _cron_job(job_id, prompt=kwargs.get("prompt") or "old")
        self.jobs[job_id] = job
        return job

    async def cancel_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None or not job.is_active:
            return False
        self.jobs.pop(job_id)
        return True

    async def delete_job(self, job_id: str) -> bool:
        self.deleted.append(job_id)
        return self.jobs.pop(job_id, None) is not None


@pytest.mark.asyncio
async def test_cron_update_and_delete_tools_use_scheduler_api() -> None:
    api = _FakeAPI()
    api.scheduler_service = _FakeScheduler()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())
    token = current_session.set(
        SessionContext(
            platform="telegram",
            chat_id="c1",
            session_id="telegram:private:c1",
            chat_address=ChatAddress(
                channel="telegram", target_type="private", target_id="c1"
            ),
        )
    )
    try:
        updated = await plugin._cron_tools.update(
            "job1",
            prompt="new prompt",
            interval_seconds=180,
            max_runs=3,
        )
        deleted = await plugin._cron_tools.delete("job1")
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


@pytest.mark.asyncio
async def test_cron_create_records_creator_and_source_session() -> None:
    api = _FakeAPI()
    api.scheduler_service = _FakeScheduler()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())
    token = current_session.set(
        SessionContext(
            platform="telegram",
            chat_id="c1",
            session_id="telegram:private:c1:abc",
            chat_address=ChatAddress(
                channel="telegram", target_type="private", target_id="c1"
            ),
            user_id="u1",
        )
    )
    try:
        result = await plugin._cron_tools.create(
            prompt="ping",
            mode="interval",
            interval_seconds=120,
        )
    finally:
        current_session.reset(token)

    assert "Scheduled task created" in result
    assert api.scheduler_service.created["created_by_user_id"] == "u1"
    assert api.scheduler_service.created["created_from_session_id"] == (
        "telegram:private:c1:abc"
    )
    assert api.scheduler_service.created["created_from_chat_address"] == (
        "telegram:private:c1"
    )


@pytest.mark.asyncio
async def test_cron_once_normalizes_naive_datetime_to_utc() -> None:
    api = _FakeAPI()
    api.scheduler_service = _FakeScheduler()
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())
    token = current_session.set(
        SessionContext(
            platform="telegram",
            chat_id="c1",
            session_id="telegram:private:c1",
            chat_address=ChatAddress(
                channel="telegram", target_type="private", target_id="c1"
            ),
        )
    )
    try:
        result = await plugin._cron_tools.create(
            "ping",
            "once",
            fire_at="2026-08-13T09:00:00",
        )
    finally:
        current_session.reset(token)

    assert "Scheduled task created" in result
    assert api.scheduler_service.created["fire_at"] == "2026-08-13T09:00:00+00:00"


@pytest.mark.asyncio
async def test_cron_cancel_enforces_current_user_ownership() -> None:
    api = _FakeAPI()
    scheduler = _FakeScheduler()
    scheduler.jobs["job1"] = _cron_job(created_by_user_id="u1")
    api.scheduler_service = scheduler
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())
    token = current_session.set(
        SessionContext(
            platform="telegram",
            chat_id="c1",
            session_id="telegram:private:c1",
            chat_address=ChatAddress(
                channel="telegram", target_type="private", target_id="c1"
            ),
            user_id="u1",
        )
    )
    try:
        cancelled = await plugin._cron_tools.cancel("job1")
    finally:
        current_session.reset(token)

    assert cancelled == "Cancelled task job1."
    assert "job1" not in scheduler.jobs


@pytest.mark.asyncio
async def test_cron_tools_hide_jobs_owned_by_other_user() -> None:
    api = _FakeAPI()
    scheduler = _FakeScheduler()
    scheduler.jobs["job1"] = _cron_job(created_by_user_id="u2")
    api.scheduler_service = scheduler
    plugin = BuiltinCommandsPlugin(api=api, manifest=_manifest())
    token = current_session.set(
        SessionContext(
            platform="telegram",
            chat_id="c1",
            session_id="telegram:private:c1",
            chat_address=ChatAddress(
                channel="telegram", target_type="private", target_id="c1"
            ),
            user_id="u1",
        )
    )
    try:
        listed = await plugin._cron_tools.list_active()
        deleted = await plugin._cron_tools.delete("job1")
    finally:
        current_session.reset(token)

    assert listed == "No active scheduled tasks for this chat."
    assert deleted == "Error: Job 'job1' does not belong to this chat."
    assert scheduler.deleted == []
