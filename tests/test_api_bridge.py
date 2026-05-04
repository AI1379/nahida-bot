"""Tests for RealBotAPI bridge behavior."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from nahida_bot.agent.providers.registry import (
    clear_runtime_providers,
    create_provider,
    unregister_runtime_provider,
)
from nahida_bot.agent.memory.models import ConversationTurn, MemoryRecord
from nahida_bot.core.events import Event, EventBus, EventContext
from nahida_bot.plugins.api_bridge import RealBotAPI
from nahida_bot.plugins.base import OutboundMessage
from nahida_bot.plugins.commands import CommandRegistry
from nahida_bot.plugins.manifest import (
    FilesystemPermission,
    MemoryPermission,
    NetworkPermission,
    Permissions,
    PluginManifest,
)
from nahida_bot.plugins.permissions import PermissionChecker
from nahida_bot.plugins.registry import HandlerRegistry, ToolRegistry
from nahida_bot.workspace.manager import WorkspaceManager

from .helpers import StubChannelService


class _Logger:
    def exception(self, event: str, **kwargs: object) -> None:
        pass

    def warning(self, event: str, **kwargs: object) -> None:
        pass


class _Memory:
    def __init__(self) -> None:
        self.meta: dict[str, Any] = {}

    async def search(
        self, session_id: str, query: str, *, limit: int = 10
    ) -> list[MemoryRecord]:
        return [
            MemoryRecord(
                turn_id=1,
                session_id=session_id,
                turn=ConversationTurn(
                    role="assistant",
                    content=f"found {query}",
                    source="memory",
                    created_at=datetime.now(UTC),
                ),
            )
        ][:limit]

    async def clear_session(self, session_id: str) -> int:
        return 3

    async def get_session_meta(self, session_id: str) -> dict[str, Any]:
        return dict(self.meta)

    async def ensure_session(
        self, session_id: str, workspace_id: str | None = None
    ) -> None:
        pass

    async def update_session_meta(
        self, session_id: str, updates: dict[str, Any]
    ) -> None:
        self.meta.update(updates)


class _ChannelRegistry:
    def __init__(self) -> None:
        self.sent: list[tuple[str, OutboundMessage]] = []
        self.channels: dict[str, Any] = {}

    def register(self, channel: Any) -> None:
        self.channels[channel.channel_id] = channel

    def unregister(self, channel_id: str) -> None:
        self.channels.pop(channel_id, None)

    def get(self, channel: str) -> Any:
        if channel in self.channels:
            return self.channels[channel]
        return self if channel == "telegram" else None

    async def send_message(self, target: str, message: OutboundMessage) -> str:
        self.sent.append((target, message))
        return "platform-msg-1"


class _ProviderManager:
    def __init__(self) -> None:
        self.slot = SimpleNamespace(id="p1")

    def list_available(self) -> list[dict[str, str]]:
        return [{"provider_id": "p1", "model": "model-a"}]

    def resolve_model(self, model_name: str) -> Any:
        if model_name == "model-a":
            return self.slot
        return None


def _manifest() -> PluginManifest:
    return PluginManifest(
        id="bridge-test",
        name="Bridge Test",
        version="1.0.0",
        entrypoint="x:Y",
        permissions=Permissions(
            network=NetworkPermission(outbound=["chat-*"]),
            filesystem=FilesystemPermission(read=["workspace"], write=["workspace"]),
            memory=MemoryPermission(read=True, write=True),
        ),
    )


def _api(
    tmp_path: Path,
    *,
    manifest: PluginManifest | None = None,
) -> tuple[RealBotAPI, _ChannelRegistry, ToolRegistry, CommandRegistry]:
    manifest = manifest or _manifest()
    event_bus = EventBus(
        EventContext(
            app=cast(Any, SimpleNamespace()),
            settings=cast(Any, SimpleNamespace()),
            logger=_Logger(),
        )
    )
    workspace = WorkspaceManager(tmp_path / "workspace")
    workspace.initialize()
    channel_registry = _ChannelRegistry()
    tool_registry = ToolRegistry()
    command_registry = CommandRegistry()
    api = RealBotAPI(
        plugin_id=manifest.id,
        manifest=manifest,
        event_bus=event_bus,
        workspace_manager=workspace,
        memory_store=cast(Any, _Memory()),
        permission_checker=PermissionChecker(manifest),
        tool_registry=tool_registry,
        handler_registry=HandlerRegistry(),
        command_registry=command_registry,
        channel_registry=channel_registry,
        provider_manager=_ProviderManager(),
    )
    return api, channel_registry, tool_registry, command_registry


@pytest.mark.asyncio
async def test_send_message_uses_channel_when_available(tmp_path: Path) -> None:
    api, channel_registry, _, _ = _api(tmp_path)

    msg_id = await api.send_message(
        "chat-123", OutboundMessage(text="hello"), channel="telegram"
    )

    assert msg_id == "platform-msg-1"
    assert channel_registry.sent[0][1].text == "hello"


@pytest.mark.asyncio
async def test_workspace_and_memory_methods_delegate_to_runtime(
    tmp_path: Path,
) -> None:
    api, _, _, _ = _api(tmp_path)

    await api.workspace_write("notes/a.txt", "hello")
    assert await api.workspace_read("notes/a.txt") == "hello"

    results = await api.memory_search("nahida")
    assert results[0].content == "found nahida"
    await api.memory_store("k", "v")
    assert await api.clear_session("s1") == 3


def test_tool_and_command_registration(tmp_path: Path) -> None:
    async def _tool(query: str) -> str:
        return query

    async def _command(**kwargs: object) -> str:
        return "ok"

    api, _, tool_registry, command_registry = _api(tmp_path)

    api.register_tool("search", "Search", {"type": "object"}, _tool)
    api.register_command("ping", _command, description="Ping", aliases=["p"])

    assert tool_registry.get("search") is not None
    assert command_registry.get("ping") is not None
    assert command_registry.get("p") is not None


def test_channel_service_registration_lifecycle(tmp_path: Path) -> None:
    api, channel_registry, _, _ = _api(tmp_path)
    channel = StubChannelService(channel_id="custom")

    api.register_channel(channel)
    assert channel_registry.get("custom") is channel

    api.deactivate_service_registrations()
    assert channel_registry.get("custom") is None

    api.reactivate_service_registrations()
    assert channel_registry.get("custom") is channel

    api.clear_service_registrations()
    assert channel_registry.get("custom") is None


def test_register_channel_rejects_non_channel_service(tmp_path: Path) -> None:
    api, _, _, _ = _api(tmp_path)

    with pytest.raises(TypeError, match="ChannelService"):
        api.register_channel(cast(Any, SimpleNamespace(channel_id="custom")))


def test_register_provider_type_requires_pre_agent_phase(tmp_path: Path) -> None:
    api, _, _, _ = _api(tmp_path)

    with pytest.raises(RuntimeError, match="pre-agent"):
        api.register_provider_type("runtime-provider", lambda config: cast(Any, None))


def test_register_provider_type_allows_pre_agent_plugin(tmp_path: Path) -> None:
    provider_type = "bridge-test-runtime-provider"
    unregister_runtime_provider(provider_type)
    manifest = _manifest().model_copy(
        update={"load_phase": "pre-agent", "id": "bridge-test-provider"}
    )
    api, _, _, _ = _api(tmp_path, manifest=manifest)

    class _RuntimeProvider:
        def __init__(self, config: dict[str, Any]) -> None:
            self.config = config

    try:
        api.register_provider_type(
            provider_type,
            lambda config: cast(Any, _RuntimeProvider(config)),
        )
        provider = cast(Any, create_provider(provider_type, model="demo-model"))
        assert provider.config["model"] == "demo-model"
    finally:
        clear_runtime_providers(owner_plugin_id=manifest.id)


@pytest.mark.asyncio
async def test_event_subscription_and_cleanup(tmp_path: Path) -> None:
    api, _, _, _ = _api(tmp_path)
    seen: list[str] = []

    async def _handler(event: Event[str]) -> None:
        seen.append(event.payload)

    api.subscribe(Event, _handler)
    await api.publish_event(Event(payload="hello"))
    assert seen == ["hello"]

    api.clear_subscriptions()
    await api.publish_event(Event(payload="again"))
    assert seen == ["hello"]


@pytest.mark.asyncio
async def test_provider_model_helpers(tmp_path: Path) -> None:
    api, _, _, _ = _api(tmp_path)

    assert api.list_models() == [{"provider_id": "p1", "model": "model-a"}]
    assert await api.set_session_model("s1", "model-a") == "p1"
    assert await api.set_session_model("s1", "missing") is None
    assert await api.get_session_info("s1") == {
        "provider_id": "p1",
        "model": "model-a",
    }
