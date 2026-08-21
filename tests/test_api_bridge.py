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
from nahida_bot.agent.memory.models import (
    ConversationTurn,
    MemoryItem,
    MemoryRecord,
    Sensitivity,
    SensitivitySource,
)
from nahida_bot.core.exceptions import PermissionDenied
from nahida_bot.core.events import (
    AgentResponseRequested,
    Event,
    EventBus,
    EventContext,
)
from nahida_bot.core.temp_files import ManagedTempFileService
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
from nahida_bot.gateway.services.webhost import WebHostService
from nahida_bot_sdk import WebhookResponse

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
        sensitivity: Sensitivity = "private",
        sensitivity_source: SensitivitySource = "default",
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
            sensitivity_source=sensitivity_source,
            evidence=evidence or {},
            metadata=metadata or {},
        )
        self.items.append(item)
        return item.item_id

    async def search_items(
        self,
        query: str = "",
        *,
        limit: int = 10,
        scope_type: str = "global",
        scope_id: str = "__global__",
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

    async def get_items_by_ids(self, item_ids: list[str]) -> list[MemoryItem]:
        by_id = {item.item_id: item for item in self.items}
        return [by_id[item_id] for item_id in item_ids if item_id in by_id]

    async def archive_item(self, item_id: str) -> bool:
        for index, item in enumerate(self.items):
            if item.item_id == item_id:
                self.items.pop(index)
                return True
        return False

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
    memory_store: Any | None = None,
    memory_soft_scope: bool = False,
) -> tuple[RealBotAPI, _ChannelRegistry, ToolRegistry, CommandRegistry]:
    manifest = manifest or _manifest()
    app = SimpleNamespace(
        settings=SimpleNamespace(
            multimodal=SimpleNamespace(image_fallback_model="vision-provider/model")
        )
    )
    event_bus = EventBus(
        EventContext(
            app=cast(Any, app),
            settings=cast(Any, SimpleNamespace()),
            logger=_Logger(),
        )
    )
    workspace = WorkspaceManager(tmp_path / "workspace")
    workspace.initialize()
    channel_registry = _ChannelRegistry()
    temp_file_service = ManagedTempFileService(tmp_path / "plugin_temp")
    webhost_service = WebHostService()
    tool_registry = ToolRegistry()
    command_registry = CommandRegistry()
    api = RealBotAPI(
        plugin_id=manifest.id,
        manifest=manifest,
        event_bus=event_bus,
        workspace_manager=workspace,
        memory_store=cast(Any, memory_store or _Memory()),
        memory_soft_scope=memory_soft_scope,
        permission_checker=PermissionChecker(manifest),
        tool_registry=tool_registry,
        handler_registry=HandlerRegistry(),
        command_registry=command_registry,
        channel_registry=channel_registry,
        webhost_service=webhost_service,
        temp_file_service=temp_file_service,
        provider_manager=_ProviderManager(),
        model_router=None,
    )
    return api, channel_registry, tool_registry, command_registry


def test_get_multimodal_image_fallback_model(tmp_path: Path) -> None:
    api, _, _, _ = _api(tmp_path)

    assert api.get_multimodal_image_fallback_model() == "vision-provider/model"


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
async def test_managed_temp_file_is_cleaned_after_send(tmp_path: Path) -> None:
    api, _, _, _ = _api(tmp_path)
    temp_file = await api.create_temp_file(
        suffix=".png",
        prefix="test-image",
        purpose="test",
        ttl_seconds=3600,
    )
    path = Path(temp_file.path)
    path.write_bytes(b"image")
    meta_path = path.with_name(f"{path.name}.meta.json")

    attachment = temp_file.as_attachment(type="photo", mime_type="image/png")
    await api.send_message(
        "chat-123",
        OutboundMessage(text="hello", attachments=[attachment]),
        channel="telegram",
    )

    assert not path.exists()
    assert not meta_path.exists()


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
    item_id = await api.memory_store("k", "v", metadata={"kind": "preference"})
    assert item_id == "mem_1"
    stored = await api.memory_search("v")
    assert stored[0].key == "mem_1"
    assert stored[0].content == "v"
    assert stored[0].metadata["kind"] == "preference"

    replacement_id = await api.memory_update(
        item_id,
        "updated value",
        key="updated key",
        metadata={"kind": "fact"},
    )
    assert replacement_id == "mem_2"
    updated = await api.memory_search("updated value")
    assert updated[0].key == "mem_2"
    assert updated[0].content == "updated value"
    assert updated[0].metadata["kind"] == "fact"
    assert await api.memory_archive(replacement_id) is True
    assert await api.memory_archive(replacement_id) is False
    assert await api.clear_session("s1") == 3


@pytest.mark.asyncio
async def test_workspace_tools_bind_to_session_workspace_id(tmp_path: Path) -> None:
    """Issue #40: workspace_read/write resolve the session-bound workspace.

    With ``current_session`` bound to ``project-b`` while the process-global
    active workspace is ``default``, the workspace tools must read/write under
    ``project-b``. Previously they ignored the session binding and used the
    mutable global active workspace, so concurrent subagent runs on different
    workspaces clobbered each other's files.
    """
    from nahida_bot.core.context import SessionContext, current_session

    api, _, _, _ = _api(tmp_path)
    # Create a second workspace and switch the global active to default.
    api._workspace.create_workspace("project-b")  # type: ignore[attr-defined]
    api._workspace.switch_workspace("default")  # type: ignore[attr-defined]

    token = current_session.set(
        SessionContext(
            platform="agent",
            chat_id="task1",
            session_id="agent:subagent:task1",
            workspace_id="project-b",
        )
    )
    try:
        await api.workspace_write("report.md", "from-subagent")
        # Global active is still default; the write must land in project-b.
        assert (
            api._workspace.workspace_path("project-b") / "report.md"  # type: ignore[attr-defined]
        ).read_text(encoding="utf-8") == "from-subagent"
        assert not (
            api._workspace.workspace_path("default") / "report.md"  # type: ignore[attr-defined]
        ).exists()
        # And read resolves the same session-bound root.
        assert await api.workspace_read("report.md") == "from-subagent"
        assert api.get_workspace_root() == str(
            api._workspace.workspace_path("project-b")  # type: ignore[attr-defined]
        )
    finally:
        current_session.reset(token)


@pytest.mark.asyncio
async def test_workspace_tools_fallback_to_active_without_session(
    tmp_path: Path,
) -> None:
    """Issue #40: no session context ⇒ legacy active-workspace behaviour."""
    from nahida_bot.core.context import current_session

    api, _, _, _ = _api(tmp_path)
    api._workspace.create_workspace("fallback-ws")  # type: ignore[attr-defined]
    api._workspace.switch_workspace("fallback-ws")  # type: ignore[attr-defined]

    # No session set.
    assert current_session.get() is None
    await api.workspace_write("plain.txt", "legacy")
    assert (
        api._workspace.workspace_path("fallback-ws") / "plain.txt"  # type: ignore[attr-defined]
    ).read_text(encoding="utf-8") == "legacy"


@pytest.mark.asyncio
async def test_memory_store_defaults_to_soft_public(tmp_path: Path) -> None:
    """Plugin writes default to the soft public baseline (Piece A4)."""
    api, _, _, _ = _api(tmp_path)
    await api.memory_store("note", "a soft recallable fact")
    item = cast(Any, api)._memory.items[-1]
    assert item.sensitivity == "public"
    assert item.sensitivity_source == "default"


@pytest.mark.asyncio
async def test_memory_store_explicit_sensitivity_is_explicit(tmp_path: Path) -> None:
    """An explicit sensitivity is recorded with source='explicit' (Piece A4)."""
    api, _, _, _ = _api(tmp_path)
    await api.memory_store(
        "secret note", "keep this between us", metadata={"sensitivity": "private"}
    )
    item = cast(Any, api)._memory.items[-1]
    assert item.sensitivity == "private"
    assert item.sensitivity_source == "explicit"


@pytest.mark.asyncio
async def test_memory_store_invalid_sensitivity_falls_back(tmp_path: Path) -> None:
    """An invalid sensitivity value falls back to public/default, not rejected."""
    api, _, _, _ = _api(tmp_path)
    await api.memory_store("n", "v", metadata={"sensitivity": "top_secret"})
    item = cast(Any, api)._memory.items[-1]
    assert item.sensitivity == "public"
    assert item.sensitivity_source == "default"


class _ScopedMemoryStore:
    """Minimal store mock for memory_search soft-scope tests (review #2)."""

    def __init__(self) -> None:
        self.public_calls = 0

    async def search_items(
        self, query: str, *, scope_type: str, scope_id: str, limit: int
    ) -> list[Any]:
        return []  # the identity cascade finds nothing

    async def search_items_public_all_scopes(
        self, query: str, *, limit: int
    ) -> list[Any]:
        self.public_calls += 1
        return [
            SimpleNamespace(
                item_id="mem_pub",
                content="public cross-scope fact",
                score=0.5,
                scope_type="chat",
                scope_id="chatB",
                kind="fact",
                title="",
                source="consolidation",
            )
        ]


@pytest.mark.asyncio
async def test_memory_search_soft_scope_supplements_cross_scope_public(
    tmp_path: Path,
) -> None:
    """memory_search admits cross-scope public items when soft_scope is on (#2)."""
    store = _ScopedMemoryStore()
    api, _, _, _ = _api(tmp_path, memory_store=store, memory_soft_scope=True)
    results = await api.memory_search("fact", limit=5)
    assert store.public_calls == 1
    assert results and results[0].key == "mem_pub"
    assert results[0].content == "public cross-scope fact"


@pytest.mark.asyncio
async def test_memory_search_soft_scope_off_skips_cross_scope(tmp_path: Path) -> None:
    """With soft_scope off, memory_search does NOT query the public all-scope pool."""
    store = _ScopedMemoryStore()
    api, _, _, _ = _api(tmp_path, memory_store=store, memory_soft_scope=False)
    await api.memory_search("fact", limit=5)
    assert store.public_calls == 0


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


@pytest.mark.asyncio
async def test_webhook_endpoint_registration_lifecycle(tmp_path: Path) -> None:
    api, _, _, _ = _api(tmp_path)

    async def _handler(request):
        return WebhookResponse(status_code=202, body=request.path)

    api.register_webhook_endpoint("github", _handler)
    webhost = cast(Any, api)._webhost_service

    not_active = await webhost.dispatch(
        path="github",
        method="POST",
        headers={},
        query={},
        body=b"",
    )
    assert not_active.status_code == 404

    api.activate_registrations()
    active = await webhost.dispatch(
        path="github",
        method="POST",
        headers={},
        query={},
        body=b"",
    )
    assert active.status_code == 202
    assert active.body == "github"

    api.deactivate_registrations()
    inactive = await webhost.dispatch(
        path="github",
        method="POST",
        headers={},
        query={},
        body=b"",
    )
    assert inactive.status_code == 404

    api.activate_registrations()
    api.clear_registrations()
    cleared = await webhost.dispatch(
        path="github",
        method="POST",
        headers={},
        query={},
        body=b"",
    )
    assert cleared.status_code == 404


def test_register_webhook_requires_inbound_permission(tmp_path: Path) -> None:
    manifest = _manifest().model_copy(
        update={
            "permissions": Permissions(
                network=NetworkPermission(outbound=["chat-*"], inbound=False),
            )
        }
    )
    api, _, _, _ = _api(tmp_path, manifest=manifest)

    async def _handler(request):
        return WebhookResponse()

    with pytest.raises(PermissionDenied, match="inbound network permission"):
        api.register_webhook_endpoint("github", _handler)


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
