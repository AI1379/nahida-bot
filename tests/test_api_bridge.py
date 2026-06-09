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
from nahida_bot.agent.memory.models import ConversationTurn, MemoryItem, MemoryRecord
from nahida_bot.core.exceptions import PermissionDenied
from nahida_bot.core.events import (
    AgentResponseRequested,
    Event,
    EventBus,
    EventContext,
)
from nahida_bot.plugins.api_bridge import RealBotAPI
from nahida_bot.plugins.base import ChatContext, InboundMessage, OutboundMessage
from nahida_bot.plugins.commands import CommandRegistry
from nahida_bot.plugins.manifest import (
    Capabilities,
    FilesystemPermission,
    MemoryPermission,
    NetworkPermission,
    Permissions,
    PluginDataPermission,
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
        self.items: list[MemoryItem] = []

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

    async def append_item(
        self,
        *,
        title: str = "",
        content: str,
        scope_type: str = "global",
        scope_id: str = "__global__",
        kind: str = "fact",
        source: str = "plugin",
        confidence: float = 1.0,
        importance: float = 0.5,
        sensitivity: str = "private",
        evidence: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        item = MemoryItem(
            item_id=f"mem_{len(self.items) + 1}",
            scope_type=scope_type,
            scope_id=scope_id,
            kind=kind,
            title=title,
            content=content,
            source=source,
            confidence=confidence,
            importance=importance,
            sensitivity=sensitivity,
            evidence=evidence or {},
            metadata=metadata or {},
        )
        self.items.append(item)
        return item.item_id

    async def search_items(
        self, query: str = "", *, limit: int = 10
    ) -> list[MemoryItem]:
        if self.items:
            return self.items[:limit]
        return [
            MemoryItem(
                item_id="mem_1",
                scope_type="global",
                scope_id="__global__",
                kind="fact",
                title="",
                content=f"found {query}",
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
        self.slot = SimpleNamespace(id="p1", default_model="model-a")
        self.slot_p2 = SimpleNamespace(id="p2", default_model="model-b")

    @property
    def default(self) -> Any:
        return self.slot

    def list_available(self) -> list[dict[str, str]]:
        return [
            {"provider_id": "p1", "model": "model-a"},
            {"provider_id": "p2", "model": "model-b"},
        ]

    def resolve_model(self, model_name: str) -> Any:
        if model_name == "model-a":
            return self.slot
        return None

    def resolve_model_selection(self, model_name: str) -> Any:
        if model_name == "model-a":
            return self.slot, "model-a"
        if model_name == "p2/model-b":
            return self.slot_p2, "model-b"
        if model_name == "model-b":
            return self.slot_p2, "model-b"
        return None


def _manifest() -> PluginManifest:
    return PluginManifest(
        id="bridge-test",
        name="Bridge Test",
        version="1.0.0",
        entrypoint="x:Y",
        permissions=Permissions(
            network=NetworkPermission(outbound=["chat-*"], inbound=True),
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
        model_router=None,
    )
    return api, channel_registry, tool_registry, command_registry


@pytest.mark.asyncio
async def test_plugin_data_raises_when_repository_unavailable(tmp_path: Path) -> None:
    manifest = PluginManifest(
        id="bridge-test",
        name="Bridge Test",
        version="1.0.0",
        entrypoint="x:Y",
        permissions=Permissions(
            plugin_data=PluginDataPermission(read=True, write=True),
        ),
    )
    api, _, _, _ = _api(tmp_path, manifest=manifest)

    with pytest.raises(RuntimeError, match="Plugin data store is not available"):
        await api.plugin_data_set("key", {"value": 1})


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
    resolved = Path(api.resolve_workspace_path("notes/a.txt"))
    assert resolved.is_absolute()
    assert resolved.name == "a.txt"
    assert resolved.read_text(encoding="utf-8") == "hello"

    results = await api.memory_search("nahida")
    assert results[0].content == "found nahida"
    await api.memory_store("k", "v", metadata={"kind": "preference"})
    stored = await api.memory_search("v")
    assert stored[0].key == "mem_1"
    assert stored[0].content == "v"
    assert stored[0].metadata["kind"] == "preference"
    assert await api.clear_session("s1") == 3


def test_tool_and_command_registration(tmp_path: Path) -> None:
    async def _tool(query: str) -> str:
        return query

    async def _command(**kwargs: object) -> str:
        return "ok"

    api, _, tool_registry, command_registry = _api(tmp_path)

    api.register_tool("search", "Search", {"type": "object"}, _tool)
    api.register_command("ping", _command, description="Ping", aliases=["p"])

    assert tool_registry.get("search") is None
    assert command_registry.get("ping") is None

    api.activate_registrations()
    assert tool_registry.get("search") is not None
    assert command_registry.get("ping") is not None
    assert command_registry.get("p") is not None

    api.deactivate_registrations()
    assert tool_registry.get("search") is None
    assert command_registry.get("ping") is None

    api.activate_registrations()
    assert tool_registry.get("search") is not None
    assert command_registry.get("ping") is not None


def test_active_tool_reregistration_replaces_owned_definition(tmp_path: Path) -> None:
    async def _tool_v1(query: str) -> str:
        return f"v1:{query}"

    async def _tool_v2(query: str) -> str:
        return f"v2:{query}"

    api, _, tool_registry, _ = _api(tmp_path)

    api.register_tool("search", "Search", {"type": "object"}, _tool_v1)
    api.activate_registrations()
    assert tool_registry.get("search").handler is _tool_v1  # type: ignore[union-attr]

    api.deactivate_registrations()
    assert tool_registry.get("search") is None

    api.activate_registrations()
    api.register_tool("search", "Search", {"type": "object"}, _tool_v2)

    entry = tool_registry.get("search")
    assert entry is not None
    assert entry.handler is _tool_v2


def test_inactive_tool_reregistration_still_rejects_duplicates(
    tmp_path: Path,
) -> None:
    async def _tool(query: str) -> str:
        return query

    api, _, _, _ = _api(tmp_path)

    api.register_tool("search", "Search", {"type": "object"}, _tool)
    with pytest.raises(KeyError, match="already registered"):
        api.register_tool("search", "Search", {"type": "object"}, _tool)


def test_command_registration_rejects_alias_conflicts_before_activation(
    tmp_path: Path,
) -> None:
    async def _command(**kwargs: object) -> str:
        return "ok"

    api, _, _, _ = _api(tmp_path)

    api.register_command("foo", _command, aliases=["bar"])
    with pytest.raises(KeyError, match="already registered"):
        api.register_command("bar", _command)
    with pytest.raises(KeyError, match="duplicated"):
        api.register_command("baz", _command, aliases=["baz"])


def test_channel_service_registration_lifecycle(tmp_path: Path) -> None:
    api, channel_registry, _, _ = _api(tmp_path)
    channel = StubChannelService(channel_id="custom")

    api.register_channel(channel)
    assert channel_registry.get("custom") is None

    api.activate_registrations()
    assert channel_registry.get("custom") is channel

    api.deactivate_registrations()
    assert channel_registry.get("custom") is None

    api.activate_registrations()
    assert channel_registry.get("custom") is channel

    api.clear_registrations()
    assert channel_registry.get("custom") is None


def test_register_channel_rejects_non_channel_service(tmp_path: Path) -> None:
    api, _, _, _ = _api(tmp_path)

    with pytest.raises(TypeError, match="ChannelService"):
        api.register_channel(cast(Any, SimpleNamespace(channel_id="custom")))


def test_register_channel_requires_inbound_permission(tmp_path: Path) -> None:
    manifest = _manifest().model_copy(
        update={
            "permissions": Permissions(
                network=NetworkPermission(outbound=["chat-*"], inbound=False),
                filesystem=FilesystemPermission(
                    read=["workspace"],
                    write=["workspace"],
                ),
                memory=MemoryPermission(read=True, write=True),
            )
        }
    )
    api, _, _, _ = _api(tmp_path, manifest=manifest)

    with pytest.raises(PermissionDenied, match="inbound network permission"):
        api.register_channel(StubChannelService(channel_id="custom"))


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
        api.activate_registrations()
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
    api.activate_registrations()
    await api.publish_event(Event(payload="hello"))
    assert seen == ["hello"]

    api.deactivate_registrations()
    await api.publish_event(Event(payload="again"))
    assert seen == ["hello"]

    api.activate_registrations()
    await api.publish_event(Event(payload="reactivated"))
    assert seen == ["hello", "reactivated"]


@pytest.mark.asyncio
async def test_event_subscription_handle_unsubscribes_permanently(
    tmp_path: Path,
) -> None:
    api, _, _, _ = _api(tmp_path)
    seen: list[str] = []

    async def _handler(event: Event[str]) -> None:
        seen.append(event.payload)

    handle = api.subscribe(Event, _handler)
    api.activate_registrations()
    await api.publish_event(Event(payload="hello"))
    handle.unsubscribe()
    await api.publish_event(Event(payload="again"))
    api.deactivate_registrations()
    api.activate_registrations()
    await api.publish_event(Event(payload="after-reactivate"))

    assert seen == ["hello"]


@pytest.mark.asyncio
async def test_request_agent_response_publishes_typed_event(tmp_path: Path) -> None:
    manifest = _manifest().model_copy(
        update={"capabilities": Capabilities(emits=["AgentResponseRequested"])}
    )
    api, _, _, _ = _api(tmp_path, manifest=manifest)
    seen: list[AgentResponseRequested] = []

    async def _handler(event: AgentResponseRequested, ctx: EventContext) -> None:
        seen.append(event)

    cast(Any, api)._event_bus.subscribe(AgentResponseRequested, _handler)
    inbound = InboundMessage(
        message_id="m1",
        platform="test",
        chat_id="g1",
        user_id="u1",
        text="join?",
        raw_event={},
        is_group=True,
        chat_context=ChatContext(platform="test", chat_type="group"),
    )
    observed = (
        InboundMessage(
            message_id="m2",
            platform="test",
            chat_id="g1",
            user_id="u2",
            text="follow-up",
            raw_event={},
            is_group=True,
            chat_context=ChatContext(platform="test", chat_type="group"),
        ),
    )

    await api.request_agent_response(
        inbound,
        session_id="test:group:g1",
        reason="good moment",
        instruction="answer briefly",
        observed_messages=observed,
        reply_to_message_id="m2",
    )

    assert len(seen) == 1
    event = seen[0]
    assert event.source == "bridge-test"
    assert event.payload.message is inbound
    assert event.payload.chat_address.chat_key == "test:group:g1"
    assert event.payload.requester_plugin_id == "bridge-test"
    assert event.payload.reason == "good moment"
    assert event.payload.instruction == "answer briefly"
    assert event.payload.observed_messages == observed
    assert event.payload.reply_to_message_id == "m2"


@pytest.mark.asyncio
async def test_request_agent_response_raises_on_router_rejection(
    tmp_path: Path,
) -> None:
    manifest = _manifest().model_copy(
        update={"capabilities": Capabilities(emits=["AgentResponseRequested"])}
    )
    api, _, _, _ = _api(tmp_path, manifest=manifest)

    async def _handler(event: AgentResponseRequested, ctx: EventContext) -> None:
        raise RuntimeError("active_run:test:group:g1")

    cast(Any, api)._event_bus.subscribe(AgentResponseRequested, _handler)
    inbound = InboundMessage(
        message_id="m1",
        platform="test",
        chat_id="g1",
        user_id="u1",
        text="join?",
        raw_event={},
        is_group=True,
        chat_context=ChatContext(platform="test", chat_type="group"),
    )

    with pytest.raises(RuntimeError, match="active_run:test:group:g1"):
        await api.request_agent_response(inbound, session_id="test:group:g1")


@pytest.mark.asyncio
async def test_request_agent_response_requires_emit_capability(
    tmp_path: Path,
) -> None:
    api, _, _, _ = _api(tmp_path)
    inbound = InboundMessage(
        message_id="m1",
        platform="test",
        chat_id="g1",
        user_id="u1",
        text="join?",
        raw_event={},
        is_group=True,
        chat_context=ChatContext(platform="test", chat_type="group"),
    )

    with pytest.raises(PermissionDenied, match="AgentResponseRequested"):
        await api.request_agent_response(inbound)


@pytest.mark.asyncio
async def test_request_agent_response_rejects_private_target(
    tmp_path: Path,
) -> None:
    manifest = _manifest().model_copy(
        update={"capabilities": Capabilities(emits=["AgentResponseRequested"])}
    )
    api, _, _, _ = _api(tmp_path, manifest=manifest)
    inbound = InboundMessage(
        message_id="m1",
        platform="test",
        chat_id="c1",
        user_id="u1",
        text="join?",
        raw_event={},
        chat_context=ChatContext(platform="test", chat_type="private"),
    )

    with pytest.raises(ValueError, match="typed group"):
        await api.request_agent_response(inbound)


@pytest.mark.asyncio
async def test_provider_model_helpers(tmp_path: Path) -> None:
    api, _, _, _ = _api(tmp_path)

    assert api.list_models() == [
        {"provider_id": "p1", "model": "model-a"},
        {"provider_id": "p2", "model": "model-b"},
    ]
    assert await api.set_session_model("s1", "model-a") == "p1"
    assert await api.set_session_model("s1", "p2/model-b") == "p2"
    assert await api.set_session_model("s1", "missing") is None
    assert await api.get_session_info("s1") == {
        "provider_id": "p2",
        "model": "model-b",
    }


@pytest.mark.asyncio
async def test_runtime_settings_merge_and_reset(tmp_path: Path) -> None:
    api, _, _, _ = _api(tmp_path)

    runtime = await api.update_runtime_settings(
        "s1", {"reasoning": {"show": True, "effort": "high"}}
    )
    assert runtime == {"reasoning": {"show": True, "effort": "high"}}
    assert await api.get_session_info("s1") == {
        "provider_id": "p1",
        "model": "model-a",
        "runtime": {"reasoning": {"show": True, "effort": "high"}},
    }

    runtime = await api.update_runtime_settings("s1", {"reasoning": {"effort": None}})
    assert runtime == {"reasoning": {"show": True}}

    runtime = await api.update_runtime_settings("s1", {"reasoning": None})
    assert runtime == {}
