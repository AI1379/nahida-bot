"""RealBotAPI — bridges the SDK-facing BotAPI protocol to bot internals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable, cast

import structlog

from nahida_bot.agent.context import MessageRole
from nahida_bot.core.chat_address import ChatAddress
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
from nahida_bot_sdk.plugin import bind_decorated_registrations

if TYPE_CHECKING:
    from nahida_bot.agent.context import ContextMessage
    from nahida_bot.agent.providers.base import ChatProvider, ProviderResponse
    from nahida_bot.agent.memory.store import MemoryStore
    from nahida_bot.core.events import EventBus
    from nahida_bot.db.repositories.sqlite_message_delivery_repo import (
        SQLiteMessageDeliveryStore,
    )
    from nahida_bot.plugins.manifest import PluginManifest
    from nahida_bot.workspace.manager import WorkspaceManager

_PROVIDER_ALLOWED_PHASES = frozenset({"pre-agent"})


@dataclass(slots=True)
class _EventRegistration:
    event_type: type
    handler: Callable[..., Awaitable[None]]
    adapted_handler: Callable[..., Awaitable[None]] | None = None
    subscription: Any | None = None
    active: bool = False


class _StoredSubscriptionHandle:
    """Stable handle for an event registration remembered by RealBotAPI."""

    def __init__(self, api: "RealBotAPI", registration: _EventRegistration) -> None:
        self._api = api
        self._registration = registration
        self._unsubscribed = False

    def unsubscribe(self) -> None:
        if self._unsubscribed:
            return
        self._api._remove_event_registration(self._registration)
        self._unsubscribed = True


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
        model_router: Any | None = None,  # ModelRouter
        scheduler_service: Any | None = None,  # SchedulerService
        orchestration_service: Any | None = None,  # AgentOrchestrator
        message_delivery_store: SQLiteMessageDeliveryStore | None = None,
    ) -> None:
        self._plugin_id = plugin_id
        self._manifest = manifest
        self._event_bus = event_bus
        self._workspace = workspace_manager
        self._memory = memory_store
        self._message_delivery_store = message_delivery_store
        self._permissions = permission_checker
        self._tool_registry = tool_registry
        self._handler_registry = handler_registry
        self._command_registry = command_registry
        self._channel_registry = channel_registry
        self._provider_manager = provider_manager
        self._model_router = model_router
        self._scheduler_service = scheduler_service
        self._orchestration_service = orchestration_service
        self._logger = _PluginLogger(plugin_id)
        self._registrations_active = False
        self._decorated_registrations_added = False
        self._registered_commands: dict[str, CommandEntry] = {}
        self._registered_command_names: dict[str, str] = {}
        self._active_commands: set[str] = set()
        self._registered_tools: dict[str, ToolEntry] = {}
        self._active_tools: set[str] = set()
        self._event_registrations: list[_EventRegistration] = []
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

    async def record_session_event(
        self,
        session_id: str,
        content: str,
        *,
        source: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        from nahida_bot.agent.memory.models import ConversationTurn

        if self._memory is None:
            return
        await self._memory.ensure_session(session_id)
        await self._memory.append_turn(
            session_id,
            ConversationTurn(
                role="system",
                content=content,
                source=source,
                metadata=metadata,
            ),
        )

    async def record_message_delivery(
        self,
        *,
        target: ChatAddress | str,
        text: str,
        source: str,
        delivery_mode: str = "",
        status: str = "sent",
        message_id: str = "",
        error: str = "",
        metadata: dict[str, Any] | None = None,
        source_session_id: str = "",
        source_chat_address: str = "",
        source_user_id: str = "",
    ) -> str:
        """Write an outbound delivery audit record without touching memory turns."""
        if self._message_delivery_store is None:
            return ""
        if isinstance(target, ChatAddress):
            address = target
        else:
            address = ChatAddress.parse(target)

        if not source_session_id or not source_chat_address or not source_user_id:
            from nahida_bot.core.context import current_session

            ctx = current_session.get()
            if ctx is not None:
                source_session_id = source_session_id or ctx.session_id
                source_user_id = source_user_id or getattr(ctx, "user_id", "")
                if not source_chat_address and ctx.chat_address is not None:
                    source_chat_address = ctx.chat_address.chat_key

        record = await self._message_delivery_store.record(
            target_chat_address=address.chat_key,
            platform=address.channel,
            target_type=address.target_type,
            target_id=address.target_id,
            source_session_id=source_session_id,
            source_chat_address=source_chat_address,
            source_user_id=source_user_id,
            source=source,
            delivery_mode=delivery_mode,
            status=status,
            message_id=message_id,
            text=text,
            error=error,
            metadata=metadata,
        )
        return record.delivery_id

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
        """Programmatic event subscription remembered across disable/enable."""
        registration = _EventRegistration(event_type=event_type, handler=handler)
        self._event_registrations.append(registration)
        if self._registrations_active:
            self._activate_event_registration(registration)
        return _StoredSubscriptionHandle(self, registration)

    # ── Tool Registration ──────────────────────────────

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Callable[..., Awaitable[str]],
    ) -> None:
        entry = ToolEntry(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            plugin_id=self._plugin_id,
        )
        if name in self._registered_tools:
            if self._registrations_active:
                self._registered_tools[name] = entry
                if name in self._active_tools:
                    self._tool_registry.unregister(name)
                    self._active_tools.discard(name)
                self._activate_tool(name)
                self._logger.debug("tool_reregistered", tool_name=name)
                return
            raise KeyError(
                f"Tool '{name}' is already registered by plugin '{self._plugin_id}'"
            )
        self._registered_tools[name] = entry
        if self._registrations_active:
            self._activate_tool(name)
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
        self._permissions.check_network_inbound()
        channel_id = channel.channel_id
        if channel_id in self._registered_channels:
            raise KeyError(
                f"Channel '{channel_id}' is already registered by plugin "
                f"'{self._plugin_id}'"
            )
        self._registered_channels[channel_id] = channel
        if self._registrations_active:
            self._activate_channel(channel_id)
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
        if type_key in self._registered_provider_types:
            raise KeyError(
                f"Provider type '{type_key}' is already registered by plugin "
                f"'{self._plugin_id}'"
            )
        self._registered_provider_types[type_key] = (
            factory,
            config_schema,
            description,
        )
        if self._registrations_active:
            self._activate_provider_type(type_key)
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
        alias_tuple = tuple(aliases or [])
        command_names = (name, *alias_tuple)
        seen_command_names: set[str] = set()
        for command_name in command_names:
            if command_name in seen_command_names:
                raise KeyError(
                    f"Command name or alias '{command_name}' is duplicated in "
                    f"plugin '{self._plugin_id}'"
                )
            seen_command_names.add(command_name)
            if command_name in self._registered_command_names:
                existing = self._registered_command_names[command_name]
                raise KeyError(
                    f"Command name or alias '{command_name}' is already "
                    f"registered by plugin '{self._plugin_id}' "
                    f"for command '{existing}'"
                )
        self._registered_commands[name] = CommandEntry(
            name=name,
            handler=handler,
            description=description,
            aliases=alias_tuple,
            plugin_id=self._plugin_id,
        )
        for command_name in command_names:
            self._registered_command_names[command_name] = name
        if self._registrations_active:
            self._activate_command(name)
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

    async def start_new_session(self, address: ChatAddress) -> str | None:
        """Switch a chat to a new active session through the message router."""
        from nahida_bot.core.router import MessageRouter

        router = self._event_bus.context.app.message_router
        if router is None:
            self._logger.warning(
                "session_new_failed",
                platform=address.channel,
                chat_id=address.target_id,
                reason="router_unavailable",
            )
            return None

        if not address.is_typed:
            self._logger.warning(
                "session_new_failed",
                platform=address.channel,
                chat_id=address.target_id,
                reason="untyped_address",
            )
            return None

        old_id = router.get_active_session_id(address)
        new_id = MessageRouter.make_new_session_id(address)
        router.set_active_session(address, new_id)
        if router.memory is not None:
            await router.memory.ensure_session(new_id)
        self._logger.debug(
            "session_new_created",
            platform=address.channel,
            chat_id=address.target_id,
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

    # ── LLM Access ──────────────────────────────────────

    async def llm_chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> Any:  # Returns LLMResponse from SDK
        """Send a single-turn chat request through the bot's provider system."""
        self._permissions.check_llm_access()

        from nahida_bot.agent.context import ContextMessage
        from nahida_bot.agent.providers.base import ToolDefinition
        from nahida_bot_sdk.api import LLMResponse, LLMUsage

        # Resolve model spec through the router
        slot = None
        resolved_model = ""
        if self._model_router is not None and model:
            routed = self._model_router.resolve(model)
            if routed is not None:
                slot = routed.slot
                resolved_model = routed.model or slot.default_model
                self._logger.debug(
                    "llm_chat_model_resolved",
                    spec=model,
                    reason=routed.reason,
                    provider_id=slot.id,
                    model=resolved_model,
                )
        elif self._provider_manager is not None:
            slot = self._provider_manager.default
            if slot is not None:
                resolved_model = slot.default_model

        if slot is None:
            raise RuntimeError(
                f"Plugin '{self._plugin_id}': no provider available for "
                f"model spec '{model}'"
            )

        # Convert simple dict messages to ContextMessage list
        ctx_messages: list[ContextMessage] = [
            ContextMessage(
                role=cast(MessageRole, m.get("role", "user")),
                content=m.get("content", ""),
                source=m.get("source", ""),
            )
            for m in messages
        ]

        # Convert tool dicts to ToolDefinition list
        tool_defs: list[ToolDefinition] | None = None
        if tools:
            tool_defs = [
                ToolDefinition(
                    name=t.get("name", ""),
                    description=t.get("description", ""),
                    parameters=t.get("parameters", {}),
                )
                for t in tools
            ]

        # Build provider kwargs
        provider_kwargs: dict[str, Any] = {
            "messages": ctx_messages,
            "model": resolved_model,
        }
        if tool_defs:
            provider_kwargs["tools"] = tool_defs

        response: ProviderResponse = await slot.provider.chat(**provider_kwargs)

        # Convert ProviderResponse to SDK LLMResponse
        usage = None
        if response.usage is not None:
            usage = LLMUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                cached_tokens=response.usage.cached_tokens,
                reasoning_tokens=response.usage.reasoning_tokens,
            )

        tool_calls: list[dict[str, Any]] = []
        for tc in response.tool_calls:
            tool_calls.append(
                {
                    "call_id": tc.call_id,
                    "name": tc.name,
                    "arguments": tc.arguments,
                }
            )

        return LLMResponse(
            content=response.content or "",
            model=resolved_model,
            provider=slot.id,
            finish_reason=response.finish_reason or "",
            usage=usage,
            tool_calls=tool_calls,
        )

    async def run_subagent(
        self,
        prompt: str,
        *,
        model: str = "",
        system_prompt: str = "",
        tools: list[str] | None = None,
        max_steps: int = 10,
        timeout_seconds: int = 300,
    ) -> Any:  # Returns SubagentResult from SDK
        """Run a multi-turn subagent with optional tool access."""
        self._permissions.check_llm_access()

        from nahida_bot.agent.orchestration.models import SubagentSpec
        from nahida_bot.core.context import current_session
        from nahida_bot_sdk.api import SubagentResult

        if self._orchestration_service is None:
            raise RuntimeError(
                f"Plugin '{self._plugin_id}': orchestration service is not available"
            )

        session_ctx = current_session.get()
        if session_ctx is None:
            raise RuntimeError(
                f"Plugin '{self._plugin_id}': run_subagent() requires an active "
                f"session context (call it from a command or event handler)"
            )

        # Resolve model spec to provider_id + model
        provider_id = ""
        resolved_model = ""
        if self._model_router is not None and model:
            routed = self._model_router.resolve(model)
            if routed is not None:
                provider_id = routed.slot.id
                resolved_model = routed.model or routed.slot.default_model
                self._logger.debug(
                    "subagent_model_resolved",
                    spec=model,
                    reason=routed.reason,
                    provider_id=provider_id,
                    model=resolved_model,
                )
        elif self._provider_manager is not None:
            default_slot = self._provider_manager.default
            if default_slot is not None:
                provider_id = default_slot.id
                resolved_model = default_slot.default_model

        spec = SubagentSpec(
            task=prompt,
            instructions=system_prompt or None,
            context_mode="isolated",
            provider_id=provider_id or None,
            model=resolved_model or None,
            timeout_seconds=timeout_seconds,
            tool_allowlist=tuple(tools or []),
            notify_policy="silent",
        )

        task = await self._orchestration_service.spawn_subagent(spec)
        completed = await self._orchestration_service.wait_for_task(
            task.task_id, timeout_seconds=timeout_seconds
        )

        if completed is None:
            return SubagentResult(
                final_response="",
                status="timed_out",
                model=resolved_model,
                provider=provider_id,
                error="Subagent timed out.",
            )

        return SubagentResult(
            final_response=completed.summary or "",
            status=completed.status.value,
            model=resolved_model,
            provider=provider_id,
            error=completed.error or "",
        )

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

    def get_session_run_status(self, session_id: str) -> dict[str, Any]:
        """Return the current agent run status for a session."""
        router = self.message_router
        if router is None:
            return {"active": False, "state": "idle", "pending_messages": 0}
        return router.get_session_run_status(session_id)

    # ── Registration Lifecycle ─────────────────────────

    def add_decorated_registrations(self, plugin: Any) -> None:
        """Remember class-level decorator registrations for this plugin instance."""
        if self._decorated_registrations_added:
            return
        bind_decorated_registrations(plugin, self)
        self._decorated_registrations_added = True

    def activate_registrations(self) -> None:
        """Activate all remembered registrations for an enabled plugin."""
        self._registrations_active = True
        for name in self._registered_commands:
            self._activate_command(name)
        for name in self._registered_tools:
            self._activate_tool(name)
        for registration in self._event_registrations:
            self._activate_event_registration(registration)
        for channel_id in self._registered_channels:
            self._activate_channel(channel_id)
        for type_key in self._registered_provider_types:
            self._activate_provider_type(type_key)

    def deactivate_registrations(self) -> None:
        """Deactivate all active registrations without forgetting them."""
        for name in list(self._active_commands):
            self._command_registry.unregister(name)
            self._active_commands.discard(name)
        for name in list(self._active_tools):
            self._tool_registry.unregister(name)
            self._active_tools.discard(name)
        self._deactivate_event_registrations(clear=False)
        for channel_id in list(self._active_channels):
            if self._channel_registry is not None:
                self._channel_registry.unregister(channel_id)
            self._active_channels.discard(channel_id)

        from nahida_bot.agent.providers.registry import unregister_runtime_provider

        for type_key in list(self._active_provider_types):
            unregister_runtime_provider(type_key, owner_plugin_id=self._plugin_id)
            self._active_provider_types.discard(type_key)
        self._registrations_active = False

    def clear_registrations(self) -> None:
        """Permanently clear all registrations owned by this plugin."""
        self.deactivate_registrations()
        self._registered_commands.clear()
        self._registered_command_names.clear()
        self._registered_tools.clear()
        self._event_registrations.clear()
        self._registered_channels.clear()
        self._registered_provider_types.clear()
        self._decorated_registrations_added = False

    def _activate_command(self, name: str) -> None:
        if name in self._active_commands:
            return
        self._command_registry.register(self._registered_commands[name])
        self._active_commands.add(name)

    def _activate_tool(self, name: str) -> None:
        if name in self._active_tools:
            return
        self._tool_registry.register(self._registered_tools[name])
        self._active_tools.add(name)

    def _activate_event_registration(self, registration: _EventRegistration) -> None:
        if registration.active:
            return

        async def _adapted(event: Any, ctx: Any) -> None:
            await registration.handler(event)

        subscription = self._event_bus.subscribe(
            registration.event_type,
            _adapted,
            priority=100,
            timeout=30.0,
        )
        registration.adapted_handler = _adapted
        registration.subscription = subscription
        registration.active = True
        self._handler_registry.register(
            HandlerEntry(
                event_type=registration.event_type,
                handler=_adapted,
                plugin_id=self._plugin_id,
            )
        )

    def _activate_channel(self, channel_id: str) -> None:
        if channel_id in self._active_channels:
            return
        if self._channel_registry is None:
            raise RuntimeError("Channel registry is not available")
        self._channel_registry.register(self._registered_channels[channel_id])
        self._active_channels.add(channel_id)

    def _activate_provider_type(self, type_key: str) -> None:
        if type_key in self._active_provider_types:
            return
        from nahida_bot.agent.providers.registry import register_runtime_provider

        factory, config_schema, description = self._registered_provider_types[type_key]
        register_runtime_provider(
            type_key,
            factory,
            description=description,
            config_schema=config_schema,
            owner_plugin_id=self._plugin_id,
        )
        self._active_provider_types.add(type_key)

    def _remove_event_registration(self, registration: _EventRegistration) -> None:
        if registration.active:
            if registration.subscription is not None:
                registration.subscription.unsubscribe()
            if registration.adapted_handler is not None:
                self._handler_registry.unregister(
                    registration.adapted_handler,
                    self._plugin_id,
                )
            registration.subscription = None
            registration.adapted_handler = None
            registration.active = False
        if registration in self._event_registrations:
            self._event_registrations.remove(registration)

    def _deactivate_event_registrations(self, *, clear: bool) -> None:
        for registration in list(self._event_registrations):
            if registration.subscription is not None:
                registration.subscription.unsubscribe()
            registration.subscription = None
            registration.adapted_handler = None
            registration.active = False
        self._handler_registry.unregister_by_plugin(self._plugin_id)
        if clear:
            self._event_registrations.clear()

    # ── Cleanup ────────────────────────────────────────

    def clear_subscriptions(self) -> None:
        """Unsubscribe all event handlers registered by this plugin."""
        self._deactivate_event_registrations(clear=True)

    def set_runtime_services(
        self,
        *,
        workspace_manager: WorkspaceManager | None = None,
        memory_store: MemoryStore | None = None,
        message_delivery_store: SQLiteMessageDeliveryStore | None = None,
        provider_manager: Any | None = None,
        model_router: Any | None = None,
        scheduler_service: Any | None = None,
        orchestration_service: Any | None = None,
    ) -> None:
        """Update runtime services after early plugin loading."""
        self._workspace = workspace_manager
        self._memory = memory_store
        self._message_delivery_store = message_delivery_store
        self._provider_manager = provider_manager
        self._model_router = model_router
        self._scheduler_service = scheduler_service
        self._orchestration_service = orchestration_service
