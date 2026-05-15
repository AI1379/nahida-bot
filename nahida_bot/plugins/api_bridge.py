"""RealBotAPI — bridges the SDK-facing BotAPI protocol to bot internals."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, Callable, cast

import structlog

from nahida_bot.plugins.base import (
    ChannelService,
    MemoryRef,
    OutboundMessage,
    PluginLogger,
    SessionInfo,
    SubscriptionHandle,
)
from nahida_bot.core.runtime_settings import (
    RUNTIME_META_KEY,
    merge_runtime_meta,
    runtime_meta_from_session_meta,
)
from nahida_bot.plugins.commands import CommandEntry, CommandHandlerResult, CommandInfo
from nahida_bot.plugins.permissions import PermissionChecker
from nahida_bot.plugins.registry import HandlerEntry, ToolEntry

if TYPE_CHECKING:
    from nahida_bot.agent.providers.base import ChatProvider
    from nahida_bot.agent.memory.store import MemoryStore
    from nahida_bot.core.events import EventBus
    from nahida_bot.plugins.manifest import PluginManifest
    from nahida_bot.workspace.manager import WorkspaceManager

_PROVIDER_ALLOWED_PHASES = frozenset({"pre-agent"})


class _PluginLogger:
    """Structured logger scoped to a single plugin."""

    def __init__(self, plugin_id: str) -> None:
        self._logger = structlog.get_logger(f"plugin.{plugin_id}")
        self._plugin_id = plugin_id

    def debug(self, msg: str, **kwargs: object) -> None:
        self._logger.debug(msg, plugin_id=self._plugin_id, **kwargs)

    def info(self, msg: str, **kwargs: object) -> None:
        self._logger.info(msg, plugin_id=self._plugin_id, **kwargs)

    def warning(self, msg: str, **kwargs: object) -> None:
        self._logger.warning(msg, plugin_id=self._plugin_id, **kwargs)

    def error(self, msg: str, **kwargs: object) -> None:
        self._logger.error(msg, plugin_id=self._plugin_id, **kwargs)

    def exception(self, msg: str, **kwargs: object) -> None:
        self._logger.exception(msg, plugin_id=self._plugin_id, **kwargs)


class RealBotAPI:
    """Concrete BotAPI implementation injected into each plugin instance.

    Every method first runs through PermissionChecker, then delegates
    to the real bot subsystem (EventBus, WorkspaceManager, MemoryStore).
    """

    def __init__(
        self,
        plugin_id: str,
        manifest: PluginManifest,
        event_bus: EventBus,
        workspace_manager: WorkspaceManager | None,
        memory_store: MemoryStore | None,
        permission_checker: PermissionChecker,
        tool_registry: Any,  # ToolRegistry — use Any to avoid circular import
        handler_registry: Any,  # HandlerRegistry
        command_registry: Any,  # CommandRegistry
        channel_registry: Any | None = None,  # ChannelRegistry
        provider_manager: Any | None = None,  # ProviderManager
        scheduler_service: Any | None = None,  # SchedulerService
        orchestration_service: Any | None = None,  # AgentOrchestrator
    ) -> None:
        self._plugin_id = plugin_id
        self._manifest = manifest
        self._event_bus = event_bus
        self._workspace = workspace_manager
        self._memory = memory_store
        self._permissions = permission_checker
        self._tool_registry = tool_registry
        self._handler_registry = handler_registry
        self._command_registry = command_registry
        self._channel_registry = channel_registry
        self._provider_manager = provider_manager
        self._scheduler_service = scheduler_service
        self._orchestration_service = orchestration_service
        self._logger = _PluginLogger(plugin_id)
        self._subscriptions: list[Any] = []  # EventBus Subscription objects
        self._registered_channels: dict[str, ChannelService] = {}
        self._active_channels: set[str] = set()
        self._registered_provider_types: dict[
            str,
            tuple[Callable[[dict[str, Any]], ChatProvider], dict[str, Any] | None, str],
        ] = {}
        self._active_provider_types: set[str] = set()

    # ── Messaging ──────────────────────────────────────

    async def send_message(
        self, target: str, message: OutboundMessage, *, channel: str = ""
    ) -> str:
        self._permissions.check_network_outbound(target)
        if self._channel_registry is not None and channel:
            channel_plugin = self._channel_registry.get(channel)
            if channel_plugin is not None:
                return await channel_plugin.send_message(target, message)
        self._logger.info(
            "send_message_fallback",
            target=target,
            channel=channel,
            text_length=len(message.text),
        )
        return f"msg_{self._plugin_id}_0"

    # ── Event Publishing ───────────────────────────────

    async def publish_event(self, event: Any) -> None:
        """Publish an event on the event bus."""
        await self._event_bus.publish(event)

    # ── Event System ───────────────────────────────────

    def on_event(self, event_type: type) -> Callable:
        """Decorator: register an event handler for this plugin."""

        def decorator(handler: Callable[..., Awaitable[None]]) -> Callable:
            self.subscribe(event_type, handler)
            return handler

        return decorator

    def subscribe(
        self,
        event_type: type,
        handler: Callable[..., Awaitable[None]],
    ) -> SubscriptionHandle:
        """Programmatic event subscription. Tracks for cleanup on disable."""

        # Adapt plugin handler (event-only) to EventBus handler (event, ctx)
        async def _adapted(event: Any, ctx: Any) -> None:
            await handler(event)

        sub = self._event_bus.subscribe(
            event_type, _adapted, priority=100, timeout=30.0
        )
        self._subscriptions.append(sub)

        # Track in handler registry
        self._handler_registry.register(
            HandlerEntry(
                event_type=event_type,
                handler=_adapted,
                plugin_id=self._plugin_id,
            )
        )
        return sub

    # ── Tool Registration ──────────────────────────────

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Callable[..., Awaitable[str]],
    ) -> None:
        self._tool_registry.register(
            ToolEntry(
                name=name,
                description=description,
                parameters=parameters,
                handler=handler,
                plugin_id=self._plugin_id,
            )
        )
        self._logger.debug("tool_registered", tool_name=name)

    # ── Service Registration ──────────────────────────

    def register_channel(self, channel: ChannelService) -> None:
        """Register a channel service implemented by this plugin."""
        if self._channel_registry is None:
            raise RuntimeError("Channel registry is not available")
        if not isinstance(channel, ChannelService):
            raise TypeError(
                f"register_channel() requires a ChannelService implementation, "
                f"got {type(channel).__name__!r} in plugin '{self._plugin_id}'"
            )
        channel_id = channel.channel_id
        self._channel_registry.register(channel)
        self._registered_channels[channel_id] = channel
        self._active_channels.add(channel_id)
        self._logger.debug("channel_registered", channel_id=channel_id)

    def register_provider_type(
        self,
        type_key: str,
        factory: Callable[[dict[str, Any]], ChatProvider],
        *,
        config_schema: dict[str, Any] | None = None,
        description: str = "",
    ) -> None:
        """Register a runtime Provider type for configuration lookup."""
        if self._manifest.load_phase not in _PROVIDER_ALLOWED_PHASES:
            raise RuntimeError(
                "Provider types may only be registered from pre-agent plugins "
                f"(plugin '{self._plugin_id}' has load_phase={self._manifest.load_phase!r})"
            )
        from nahida_bot.agent.providers.registry import register_runtime_provider

        register_runtime_provider(
            type_key,
            factory,
            description=description,
            config_schema=config_schema,
            owner_plugin_id=self._plugin_id,
        )
        self._registered_provider_types[type_key] = (
            factory,
            config_schema,
            description,
        )
        self._active_provider_types.add(type_key)
        self._logger.debug("provider_type_registered", provider_type=type_key)

    @property
    def scheduler_service(self) -> Any | None:
        return self._scheduler_service

    # ── Command Registration ───────────────────────────

    def register_command(
        self,
        name: str,
        handler: Callable[..., Awaitable[CommandHandlerResult]],
        *,
        description: str = "",
        aliases: list[str] | None = None,
    ) -> None:
        self._command_registry.register(
            CommandEntry(
                name=name,
                handler=handler,
                description=description,
                aliases=tuple(aliases or []),
                plugin_id=self._plugin_id,
            )
        )
        self._logger.debug("command_registered", command_name=name)

    # ── Session ────────────────────────────────────────

    async def get_session(self, session_id: str) -> SessionInfo | None:
        # Full implementation deferred to Channel integration (Phase 4)
        return None

    # ── Memory ─────────────────────────────────────────

    async def memory_search(self, query: str, *, limit: int = 5) -> list[MemoryRef]:
        self._permissions.check_memory_read()
        if self._memory is None:
            return []
        search_items = getattr(self._memory, "search_items", None)
        if callable(search_items):
            items = await cast(Any, search_items)(query, limit=limit)
            return [
                MemoryRef(
                    key=item.item_id,
                    content=item.content,
                    score=item.score,
                    metadata={
                        "scope_type": item.scope_type,
                        "scope_id": item.scope_id,
                        "kind": item.kind,
                        "title": item.title,
                        "source": item.source,
                    },
                )
                for item in items
            ]
        results = await self._memory.search("__global__", query, limit=limit)
        return [
            MemoryRef(
                key=str(r.turn_id),
                content=r.turn.content,
                metadata={"session_id": r.session_id},
            )
            for r in results
        ]

    async def memory_store(
        self, key: str, content: str, *, metadata: dict[str, Any] | None = None
    ) -> None:
        self._permissions.check_memory_write()
        if self._memory is None:
            return
        metadata = dict(metadata or {})
        append_item = getattr(self._memory, "append_item", None)
        if callable(append_item):
            await cast(Any, append_item)(
                title=key,
                content=content,
                scope_type=str(metadata.pop("scope_type", "global")),
                scope_id=str(metadata.pop("scope_id", "__global__")),
                kind=str(metadata.pop("kind", "fact")),
                source=str(metadata.pop("source", "plugin")),
                confidence=float(metadata.pop("confidence", 1.0)),
                importance=float(metadata.pop("importance", 0.5)),
                sensitivity=str(metadata.pop("sensitivity", "private")),
                evidence=metadata.pop("evidence", None),
                metadata=metadata,
            )
            self._logger.debug("memory_store_called", key=key, backend="items")
            return

        append_turn = getattr(self._memory, "append_turn", None)
        ensure_session = getattr(self._memory, "ensure_session", None)
        if callable(append_turn):
            if callable(ensure_session):
                await cast(Any, ensure_session)("__global__")
            from nahida_bot.agent.memory.models import ConversationTurn

            await cast(Any, append_turn)(
                "__global__",
                ConversationTurn(
                    role="system",
                    content=content,
                    source="plugin_memory",
                    metadata={"key": key, **metadata},
                ),
            )
        self._logger.debug("memory_store_called", key=key, backend="turns")

    # ── Workspace ──────────────────────────────────────

    async def workspace_read(self, path: str) -> str:
        self._permissions.check_filesystem_read("workspace")
        if self._workspace is None:
            return ""
        sandbox = self._workspace.get_sandbox()
        return sandbox.read_text(path)

    async def workspace_write(self, path: str, content: str) -> None:
        self._permissions.check_filesystem_write("workspace")
        if self._workspace is None:
            return
        sandbox = self._workspace.get_sandbox()
        sandbox.write_text(path, content)

    def resolve_workspace_path(self, path: str) -> str:
        """Resolve a workspace-relative path for local file attachment sends."""
        self._permissions.check_filesystem_read("workspace")
        if self._workspace is None:
            return ""
        sandbox = self._workspace.get_sandbox()
        return str(sandbox.resolve_safe_path(path))

    # ── Logging ────────────────────────────────────────

    @property
    def logger(self) -> PluginLogger:
        return self._logger

    # ── Extended Internals (for builtin plugins) ───────

    async def clear_session(self, session_id: str) -> int:
        """Delete all turns for a session. Returns deleted count."""
        if self._memory is None:
            return 0
        return await self._memory.clear_session(session_id)

    async def start_new_session(self, platform: str, chat_id: str) -> str | None:
        """Switch a chat to a new active session through the message router."""
        from nahida_bot.core.router import MessageRouter

        router = self._event_bus.context.app.message_router
        if router is None:
            self._logger.warning(
                "session_new_failed",
                platform=platform,
                chat_id=chat_id,
                reason="router_unavailable",
            )
            return None

        old_id = router.get_active_session_id(platform, chat_id)
        new_id = MessageRouter.make_new_session_id(platform, chat_id)
        router.set_active_session(platform, chat_id, new_id)
        if router.memory is not None:
            await router.memory.ensure_session(new_id)
        self._logger.debug(
            "session_new_created",
            platform=platform,
            chat_id=chat_id,
            old_session_id=old_id,
            new_session_id=new_id,
        )
        return new_id

    def list_commands(self) -> list[CommandInfo]:
        """List registered commands without exposing registry internals."""
        return [entry.to_info() for entry in self._command_registry.all_commands()]

    def list_models(self) -> list[dict[str, str]]:
        """List all available provider+model combinations."""
        if self._provider_manager is None:
            return []
        return self._provider_manager.list_available()

    async def set_session_model(self, session_id: str, model_name: str) -> str | None:
        """Switch model for a session. Returns provider id or None."""
        if self._provider_manager is None or self._memory is None:
            self._logger.debug(
                "session_model_set_skipped",
                session_id=session_id,
                requested_model=model_name,
                reason="missing_provider_manager_or_memory",
            )
            return None
        resolved = self._provider_manager.resolve_model_selection(model_name)
        if resolved is None:
            self._logger.debug(
                "session_model_not_found",
                session_id=session_id,
                requested_model=model_name,
            )
            return None
        slot, bare_name = resolved
        await self._memory.ensure_session(session_id)
        await self._memory.update_session_meta(
            session_id, {"provider_id": slot.id, "model": bare_name}
        )
        self._logger.debug(
            "session_model_set",
            session_id=session_id,
            requested_model=model_name,
            provider_id=slot.id,
            stored_model=bare_name,
            default_model=slot.default_model,
        )
        return slot.id

    async def get_session_info(self, session_id: str) -> dict[str, Any]:
        """Get session metadata and turn count.

        Falls back to the default provider slot's model info when
        the session has no explicit model preference stored.
        """
        if self._memory is None:
            return {}
        meta = await self._memory.get_session_meta(session_id)
        result = dict(meta)
        if not result.get("model") and self._provider_manager is not None:
            default_slot = self._provider_manager.default
            if default_slot is not None:
                result.setdefault("provider_id", default_slot.id)
                result.setdefault("model", default_slot.default_model)
        self._logger.debug(
            "session_info_resolved",
            session_id=session_id,
            provider_id=result.get("provider_id", ""),
            model=result.get("model", ""),
            has_explicit_meta=bool(meta),
        )
        return result

    async def update_runtime_settings(
        self, session_id: str, updates: dict[str, Any]
    ) -> dict[str, Any]:
        """Merge runtime settings into session metadata and return the result."""
        if self._memory is None:
            self._logger.debug(
                "runtime_settings_update_skipped",
                session_id=session_id,
                reason="missing_memory",
            )
            return {}
        await self._memory.ensure_session(session_id)
        meta = await self._memory.get_session_meta(session_id)
        runtime = runtime_meta_from_session_meta(meta)
        merged = merge_runtime_meta(runtime, updates)
        await self._memory.update_session_meta(session_id, {RUNTIME_META_KEY: merged})
        self._logger.debug(
            "runtime_settings_updated",
            session_id=session_id,
            keys=sorted(merged.keys()),
        )
        return merged

    def get_provider_manager(self) -> Any:
        """Access the ProviderManager (if configured)."""
        return self._provider_manager

    @property
    def orchestration_service(self) -> Any | None:
        """Access the AgentOrchestrator exposed to built-in tools."""
        return self._orchestration_service

    @property
    def message_router(self) -> Any | None:
        """Access the MessageRouter for /stop command support."""
        return self._event_bus.context.app.message_router

    # ── Cleanup ────────────────────────────────────────

    def clear_subscriptions(self) -> None:
        """Unsubscribe all event handlers registered by this plugin."""
        for sub in self._subscriptions:
            sub.unsubscribe()
        self._subscriptions.clear()

    def set_runtime_services(
        self,
        *,
        workspace_manager: WorkspaceManager | None = None,
        memory_store: MemoryStore | None = None,
        provider_manager: Any | None = None,
        scheduler_service: Any | None = None,
        orchestration_service: Any | None = None,
    ) -> None:
        """Update runtime services after early plugin loading."""
        self._workspace = workspace_manager
        self._memory = memory_store
        self._provider_manager = provider_manager
        self._scheduler_service = scheduler_service
        self._orchestration_service = orchestration_service

    def deactivate_service_registrations(self) -> None:
        """Temporarily deactivate services registered by this plugin."""
        for channel_id in list(self._active_channels):
            if self._channel_registry is not None:
                self._channel_registry.unregister(channel_id)
            self._active_channels.discard(channel_id)

        from nahida_bot.agent.providers.registry import unregister_runtime_provider

        for type_key in list(self._active_provider_types):
            unregister_runtime_provider(type_key, owner_plugin_id=self._plugin_id)
            self._active_provider_types.discard(type_key)

    def reactivate_service_registrations(self) -> None:
        """Re-register remembered services when a disabled plugin is re-enabled."""
        for channel_id, channel in self._registered_channels.items():
            if channel_id not in self._active_channels and self._channel_registry:
                self._channel_registry.register(channel)
                self._active_channels.add(channel_id)

        from nahida_bot.agent.providers.registry import register_runtime_provider

        for type_key, (
            factory,
            config_schema,
            description,
        ) in self._registered_provider_types.items():
            if type_key not in self._active_provider_types:
                register_runtime_provider(
                    type_key,
                    factory,
                    description=description,
                    config_schema=config_schema,
                    owner_plugin_id=self._plugin_id,
                )
                self._active_provider_types.add(type_key)

    def clear_service_registrations(self) -> None:
        """Permanently unregister services registered by this plugin."""
        self.deactivate_service_registrations()
        self._registered_channels.clear()
        self._registered_provider_types.clear()
