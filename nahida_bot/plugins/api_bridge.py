"""RealBotAPI — bridges the SDK-facing BotAPI protocol to bot internals."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, Callable

import structlog

from nahida_bot.plugins.base import (
    MemoryRef,
    OutboundMessage,
    PluginLogger,
    SessionInfo,
    SubscriptionHandle,
)
from nahida_bot.plugins.commands import CommandEntry
from nahida_bot.plugins.permissions import PermissionChecker
from nahida_bot.plugins.registry import HandlerEntry, ToolEntry

if TYPE_CHECKING:
    from nahida_bot.agent.memory.store import MemoryStore
    from nahida_bot.core.events import EventBus
    from nahida_bot.plugins.manifest import PluginManifest
    from nahida_bot.workspace.manager import WorkspaceManager


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
        self._logger = _PluginLogger(plugin_id)
        self._subscriptions: list[Any] = []  # EventBus Subscription objects

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

    # ── Command Registration ───────────────────────────

    def register_command(
        self,
        name: str,
        handler: Callable[..., Awaitable[str]],
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
        # Memory store integration deferred — for now this is a no-op
        self._logger.debug("memory_store_called", key=key)

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

    def list_models(self) -> list[dict[str, str]]:
        """List all available provider+model combinations."""
        if self._provider_manager is None:
            return []
        return self._provider_manager.list_available()

    async def set_session_model(self, session_id: str, model_name: str) -> str | None:
        """Switch model for a session. Returns provider id or None."""
        if self._provider_manager is None or self._memory is None:
            return None
        slot = self._provider_manager.resolve_model(model_name)
        if slot is None:
            return None
        await self._memory.ensure_session(session_id)
        await self._memory.update_session_meta(
            session_id, {"provider_id": slot.id, "model": model_name}
        )
        return slot.id

    async def get_session_info(self, session_id: str) -> dict[str, Any]:
        """Get session metadata and turn count."""
        if self._memory is None:
            return {}
        meta = await self._memory.get_session_meta(session_id)
        return dict(meta)

    def get_provider_manager(self) -> Any:
        """Access the ProviderManager (if configured)."""
        return self._provider_manager

    # ── Cleanup ────────────────────────────────────────

    def clear_subscriptions(self) -> None:
        """Unsubscribe all event handlers registered by this plugin."""
        for sub in self._subscriptions:
            sub.unsubscribe()
        self._subscriptions.clear()
