"""RealBotAPI — bridges the SDK-facing BotAPI protocol to bot internals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Coroutine, cast

import structlog

from nahida_bot.agent.context import MessageRole
from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.plugins.base import (
    AttentionFrame,
    ChannelService,
    InboundMessage,
    MemoryRef,
    OutboundMessage,
    PluginLogger,
    SessionInfo,
    SubscriptionHandle,
    WebhookHandle,
    WebhookRequest,
    WebhookResponse,
)
from nahida_bot.core.runtime_settings import (
    RUNTIME_META_KEY,
    merge_runtime_meta,
    runtime_meta_from_session_meta,
)
from nahida_bot.plugins.commands import CommandEntry, CommandHandlerResult, CommandInfo
from nahida_bot.plugins.permissions import PermissionChecker
from nahida_bot.plugins.registry import HandlerEntry, PromptSupplementEntry, ToolEntry
from nahida_bot_sdk.plugin import bind_decorated_registrations

if TYPE_CHECKING:
    from nahida_bot.agent.context import ContextMessage
    from nahida_bot.agent.providers.base import ChatProvider, ProviderResponse
    from nahida_bot.agent.memory.sqlite import SQLiteMemoryStore
    from nahida_bot.core.temp_files import ManagedTempFileService
    from nahida_bot.core.events import EventBus
    from nahida_bot.db.repositories.sqlite_message_delivery_repo import (
        SQLiteMessageDeliveryStore,
    )
    from nahida_bot.db.repositories.sqlite_plugin_data_repo import (
        SQLitePluginDataRepository,
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


@dataclass(slots=True)
class _WebhookRegistration:
    path: str
    handler: Callable[[WebhookRequest], Awaitable[WebhookResponse | None]]
    methods: tuple[str, ...]
    service_handle: Any | None = None
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


class _StoredWebhookHandle:
    """Stable handle for a webhook endpoint remembered by RealBotAPI."""

    def __init__(self, api: "RealBotAPI", registration: _WebhookRegistration) -> None:
        self._api = api
        self._registration = registration
        self._unsubscribed = False

    def unsubscribe(self) -> None:
        if self._unsubscribed:
            return
        self._api._remove_webhook_registration(self._registration)
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
    to the real bot subsystem (EventBus, WorkspaceManager, SQLiteMemoryStore).
    """

    def __init__(
        self,
        plugin_id: str,
        manifest: PluginManifest,
        event_bus: EventBus,
        workspace_manager: WorkspaceManager | None,
        memory_store: SQLiteMemoryStore | None,
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
        plugin_data_repo: SQLitePluginDataRepository | None = None,
        supplement_registry: Any | None = None,  # PromptSupplementRegistry
        status_provider_registry: Any | None = None,  # StatusProviderRegistry
        webhost_service: Any | None = None,  # WebHostService
        task_manager: Any | None = None,  # TaskManager
        document_store_manager: Any | None = None,  # DocumentStoreManager
        temp_file_service: ManagedTempFileService | None = None,
        memory_soft_scope: bool = False,
    ) -> None:
        self._plugin_id = plugin_id
        self._manifest = manifest
        self._event_bus = event_bus
        self._workspace = workspace_manager
        self._memory = memory_store
        # Whether soft-scope cross-scope public recall is on (Piece A2). Mirrors
        # the SessionRunner flag so plugin memory_search / /memory search stay
        # consistent with auto-injection. Default off = no behavior change.
        self._memory_soft_scope = memory_soft_scope
        # Lazily-built unified MemoryService over ``_memory``. All consumer
        # memory reads/writes (memory_search / memory_store / search_chat_history
        # / the /memory command) delegate here so the agent SDK and the gateway
        # REST API share one read-cascade + write + projection implementation.
        self._memory_service_cache: Any = None
        self._message_delivery_store = message_delivery_store
        self._plugin_data_repo = plugin_data_repo
        self._permissions = permission_checker
        self._tool_registry = tool_registry
        self._handler_registry = handler_registry
        self._command_registry = command_registry
        self._channel_registry = channel_registry
        self._provider_manager = provider_manager
        self._model_router = model_router
        self._scheduler_service = scheduler_service
        self._orchestration_service = orchestration_service
        self._chat_metadata_store: Any | None = None
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
        self._supplement_registry = supplement_registry
        self._registered_supplements: dict[str, PromptSupplementEntry] = {}
        self._active_supplements: set[str] = set()
        self._status_provider_registry = status_provider_registry
        self._webhost_service = webhost_service
        self._task_manager = task_manager
        self._document_store_manager = document_store_manager
        self._temp_file_service = temp_file_service
        self._registered_status_providers: dict[
            str, Any
        ] = {}  # global_key -> StatusProviderEntry
        self._active_status_providers: set[str] = set()
        self._webhook_registrations: list[_WebhookRegistration] = []

    # ── Messaging ──────────────────────────────────────

    async def identity_manage(
        self,
        action: str,
        *,
        person_id: str = "",
        display_name: str = "",
        account_key: str = "",
    ) -> dict[str, Any]:
        """Run an audited, admin-only identity management operation."""
        from nahida_bot.core.context import current_session
        from nahida_bot.identity.management import IdentityManager

        app = self._event_bus.context.app
        store = getattr(app, "_identity_store", None)
        gate = getattr(app, "_authorization_gate", None)
        ctx = current_session.get()
        actor = ctx.actor_account_key if ctx is not None else ""
        if gate is not None:
            gate.authorize("identity_manage", actor)
        if store is None:
            raise RuntimeError("identity store is not initialized")
        manager = IdentityManager(store)

        if action == "list":
            people = await store.list_people()
            return {
                "people": [
                    {
                        "person_id": person.person_id,
                        "display_name": person.display_name,
                        "accounts": [
                            item.account_key
                            for item in await store.list_accounts(person.person_id)
                        ],
                    }
                    for person in people
                ]
            }
        if action == "observations":
            observations = await store.list_observations(
                account_key=account_key,
                limit=50,
            )
            return {
                "observations": [
                    {
                        "account_key": item.account_key,
                        "chat_address": item.chat_address,
                        "display_name": item.display_name,
                    }
                    for item in observations
                ]
            }
        if action == "create":
            person = await manager.create_or_update_person(
                person_id=person_id,
                display_name=display_name,
                actor=actor,
            )
            return {"person_id": person.person_id, "display_name": person.display_name}
        if action == "link":
            link = await manager.link_account(
                account_key=account_key,
                person_id=person_id,
                actor=actor,
            )
            return {"account_key": link.account_key, "person_id": link.person_id}
        if action == "unlink":
            changed = await manager.unlink_account(account_key=account_key, actor=actor)
            return {"account_key": account_key, "unlinked": changed}
        raise ValueError(f"unknown identity action: {action}")

    async def send_message(
        self, target: str, message: OutboundMessage, *, channel: str = ""
    ) -> str:
        self._permissions.check_network_outbound(target)
        if self._channel_registry is not None and channel:
            channel_plugin = self._channel_registry.get(channel)
            if channel_plugin is not None:
                message_id = await channel_plugin.send_message(target, message)
                await self._cleanup_message_temp_files(message)
                return message_id
        self._logger.info(
            "send_message_fallback",
            target=target,
            channel=channel,
            text_length=len(message.text),
        )
        await self._cleanup_message_temp_files(message)
        return f"msg_{self._plugin_id}_0"

    async def create_temp_file(
        self,
        *,
        suffix: str = "",
        prefix: str = "",
        purpose: str = "",
        ttl_seconds: int = 3600,
    ) -> Any:
        """Allocate a plugin-scoped temporary file managed by the runtime."""
        if self._temp_file_service is None:
            raise RuntimeError("Managed temp file service is not available")
        return await self._temp_file_service.create_temp_file(
            plugin_id=self._plugin_id,
            suffix=suffix,
            prefix=prefix,
            purpose=purpose,
            ttl_seconds=ttl_seconds,
        )

    async def cleanup_temp_files(self, *, expired_only: bool = True) -> int:
        """Clean this plugin's managed temporary files."""
        if self._temp_file_service is None:
            return 0
        return await self._temp_file_service.cleanup_plugin(
            self._plugin_id,
            expired_only=expired_only,
        )

    async def cleanup_temp_attachment(self, attachment: Any) -> bool:
        """Clean one managed temporary attachment."""
        if self._temp_file_service is None:
            return False
        return await self._temp_file_service.cleanup_attachment(
            attachment,
            ignore_cleanup_after_send=True,
        )

    async def _cleanup_message_temp_files(self, message: OutboundMessage) -> None:
        if self._temp_file_service is None or not message.attachments:
            return
        try:
            removed = await self._temp_file_service.cleanup_message(message)
            if removed:
                self._logger.debug(
                    "managed_temp_file.message_cleanup",
                    removed=removed,
                    attachment_count=len(message.attachments),
                )
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                "managed_temp_file.message_cleanup_failed",
                error=str(exc),
            )

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

    async def request_agent_response(
        self,
        message: InboundMessage,
        *,
        session_id: str = "",
        reason: str = "",
        instruction: str = "",
        observed_messages: tuple[InboundMessage, ...] = (),
        reply_to_message_id: str | None = None,
        attention_frame: AttentionFrame | None = None,
    ) -> None:
        """Ask the router to run the main agent for an observed group message."""
        self._permissions.check_event_emit("AgentResponseRequested")

        from nahida_bot.core.events import (
            AgentResponseRequested,
            AgentResponseRequestPayload,
        )

        address = _address_from_inbound_message(message)
        if not address.is_typed or address.target_type != "group":
            raise ValueError(
                "request_agent_response() only supports typed group chat addresses"
            )

        payload = AgentResponseRequestPayload(
            message=message,
            session_id=session_id or address.chat_key,
            chat_address=address,
            requester_plugin_id=self._plugin_id,
            reason=str(reason or "").strip(),
            instruction=str(instruction or "").strip(),
            observed_messages=tuple(observed_messages or ()),
            reply_to_message_id=reply_to_message_id,
            attention_frame=attention_frame,
        )
        result = await self._event_bus.publish(
            AgentResponseRequested(payload=payload, source=self._plugin_id)
        )
        if result.failures:
            first = result.failures[0]
            raise RuntimeError(
                "AgentResponseRequested was rejected by "
                f"{first.handler_name}: {first.error}"
            )
        self._logger.debug(
            "agent_response_requested",
            session_id=payload.session_id,
            chat_address=address.chat_key,
            reason_chars=len(payload.reason),
            instruction_chars=len(payload.instruction),
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

    def unregister_tool(self, name: str) -> bool:
        """Remove a previously registered tool by name.

        Returns ``True`` if the tool existed and was removed, ``False`` otherwise.
        Only tools owned by this plugin can be unregistered.
        """
        entry = self._registered_tools.pop(name, None)
        if entry is None:
            return False
        if name in self._active_tools:
            self._tool_registry.unregister(name)
            self._active_tools.discard(name)
        self._logger.debug("tool_unregistered", tool_name=name)
        return True

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

    def register_webhook_endpoint(
        self,
        path: str,
        handler: Callable[[WebhookRequest], Awaitable[WebhookResponse | None]],
        *,
        methods: tuple[str, ...] = ("POST",),
    ) -> WebhookHandle:
        """Register a plugin-owned raw HTTP webhook endpoint."""
        self._permissions.check_network_inbound()
        registration = _WebhookRegistration(
            path=path,
            handler=handler,
            methods=tuple(methods),
        )
        self._webhook_registrations.append(registration)
        if self._registrations_active:
            self._activate_webhook_registration(registration)
        self._logger.debug(
            "webhook_endpoint_registered",
            path=path,
            methods=list(methods),
        )
        return _StoredWebhookHandle(self, registration)

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

    # ── Prompt Supplement Registration ─────────────────

    def register_prompt_supplement(
        self,
        key: str,
        instruction: str,
        *,
        channel: str | None = None,
        filter: Callable[..., bool] | None = None,
    ) -> None:
        """Register a supplemental instruction to inject into the system prompt."""
        global_key = f"{self._plugin_id}:{key}"
        entry = PromptSupplementEntry(
            key=global_key,
            instruction=instruction,
            plugin_id=self._plugin_id,
            channel=channel,
            filter=filter,
        )
        if global_key in self._registered_supplements:
            raise KeyError(
                f"Prompt supplement '{key}' is already registered by plugin "
                f"'{self._plugin_id}'"
            )
        self._registered_supplements[global_key] = entry
        if self._registrations_active:
            self._activate_supplement(global_key)
        self._logger.debug("prompt_supplement_registered", supplement_key=key)

    def unregister_prompt_supplement(self, key: str) -> bool:
        """Remove a previously registered prompt supplement. Returns True if found."""
        global_key = f"{self._plugin_id}:{key}"
        entry = self._registered_supplements.pop(global_key, None)
        if entry is None:
            return False
        if global_key in self._active_supplements:
            if self._supplement_registry is not None:
                self._supplement_registry.unregister(global_key)
            self._active_supplements.discard(global_key)
        self._logger.debug("prompt_supplement_unregistered", supplement_key=key)
        return True

    def _activate_supplement(self, global_key: str) -> None:
        if global_key in self._active_supplements:
            return
        if self._supplement_registry is None:
            raise RuntimeError("Prompt supplement registry is not available")
        self._supplement_registry.register(self._registered_supplements[global_key])
        self._active_supplements.add(global_key)

    # ── Status Provider Registration ──────────────────

    def register_status_provider(
        self,
        key: str,
        handler: Callable[..., Awaitable[str | None]],
        *,
        label: str = "",
    ) -> None:
        """Register a provider that contributes text to /status output."""
        from nahida_bot.plugins.registry import StatusProviderEntry

        global_key = f"{self._plugin_id}:{key}"
        entry = StatusProviderEntry(
            key=global_key,
            label=label or key,
            handler=handler,
            plugin_id=self._plugin_id,
        )
        if global_key in self._registered_status_providers:
            raise KeyError(
                f"Status provider '{key}' is already registered by plugin "
                f"'{self._plugin_id}'"
            )
        self._registered_status_providers[global_key] = entry
        if self._registrations_active:
            self._activate_status_provider(global_key)
        self._logger.debug("status_provider_registered", provider_key=key)

    def unregister_status_provider(self, key: str) -> bool:
        """Remove a previously registered status provider. Returns True if found."""
        global_key = f"{self._plugin_id}:{key}"
        entry = self._registered_status_providers.pop(global_key, None)
        if entry is None:
            return False
        if global_key in self._active_status_providers:
            if self._status_provider_registry is not None:
                self._status_provider_registry.unregister(global_key)
            self._active_status_providers.discard(global_key)
        self._logger.debug("status_provider_unregistered", provider_key=key)
        return True

    def _activate_status_provider(self, global_key: str) -> None:
        if global_key in self._active_status_providers:
            return
        if self._status_provider_registry is None:
            raise RuntimeError("Status provider registry is not available")
        self._status_provider_registry.register(
            self._registered_status_providers[global_key]
        )
        self._active_status_providers.add(global_key)

    async def collect_status_providers(
        self,
        *,
        session_id: str,
        chat_key: str,
    ) -> list[str]:
        """Collect text blocks from all registered status providers.

        Returns a list of non-None text blocks, one per provider.
        """
        if self._status_provider_registry is None:
            return []
        results: list[str] = []
        for entry in self._status_provider_registry.all():
            try:
                text = await entry.handler(session_id=session_id, chat_key=chat_key)
                if text:
                    results.append(text)
            except Exception as exc:  # noqa: BLE001
                self._logger.warning(
                    "status_provider_error",
                    provider_key=entry.key,
                    error=f"{type(exc).__name__}: {exc}",
                )
        return results

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

    def _memory_service(self) -> Any | None:
        """Lazily build the unified :class:`MemoryService` over ``self._memory``.

        Returns ``None`` when no memory store is wired. The service owns the
        identity-aware read cascade, the soft-scope public recall, the write
        policy, and the Markdown projection, so this bridge (and the future REST
        route) stay thin adapters over the same implementation.
        """
        if self._memory is None:
            return None
        if self._memory_service_cache is None:
            from nahida_bot.agent.memory.service import MemoryService

            self._memory_service_cache = MemoryService(
                self._memory,
                soft_scope=self._memory_soft_scope,
            )
        return self._memory_service_cache

    async def memory_search(self, query: str, *, limit: int = 5) -> list[MemoryRef]:
        self._permissions.check_memory_read()
        service = self._memory_service()
        if service is None:
            return []
        from nahida_bot.core.context import current_session

        session_ctx = current_session.get()
        session_id = getattr(session_ctx, "session_id", "") if session_ctx else ""
        items = await service.search_items_cascade(
            query,
            ctx=session_ctx,
            session_id=session_id,
            limit=limit,
        )
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
                    "sensitivity": getattr(item, "sensitivity", "public"),
                    "audience": getattr(item, "metadata", {}).get(
                        "audience", "current"
                    ),
                },
            )
            for item in items
        ]

    async def search_chat_history(
        self,
        query: str,
        *,
        chat_address: str = "",
        role: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search raw conversation turns across ALL sessions (soft-gated).

        Wraps the memory service's cross-session LIKE search over every role
        (user / assistant / system). Intentionally has NO permission check and
        NO scope restriction: the gating is a soft prompt in the tool
        description (memory is soft context; the worst case is saying the wrong
        thing, per person-identity-system.md §2.5). Returns raw rows; the caller
        (tool handler) sanitizes before showing the model.
        """
        service = self._memory_service()
        if service is None:
            return []
        records = await service.search_turns(
            query,
            chat_address=chat_address,
            role=role,
            limit=limit,
        )
        # Normalize MemoryRecord objects into plain dicts so tool handlers can
        # treat results uniformly without importing the memory dataclass shape.
        results: list[dict[str, Any]] = []
        for record in records:
            turn = getattr(record, "turn", None)
            created_at = getattr(turn, "created_at", None) if turn else None
            if created_at is not None and hasattr(created_at, "isoformat"):
                created_str = cast(Any, created_at).isoformat()
            else:
                created_str = str(created_at) if created_at else ""
            results.append(
                {
                    "session_id": getattr(record, "session_id", "") or "",
                    "role": getattr(turn, "role", "") if turn else "",
                    "content": getattr(turn, "content", "") if turn else "",
                    "created_at": created_str,
                }
            )
        return results

    async def read_chat_history(
        self,
        *,
        mode: str = "recent",
        chat_address: str = "",
        session_id: str = "",
        query: str = "",
        message_id: str = "",
        since: datetime | None = None,
        until: datetime | None = None,
        before_turn_id: int | None = None,
        before: int = 5,
        after: int = 5,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Read a structured chronological chat slice for agent recall."""
        self._permissions.check_memory_read()
        service = self._memory_service()
        if service is None:
            return []

        from nahida_bot.core.context import current_session

        session_ctx = current_session.get()
        if not chat_address and not session_id and session_ctx is not None:
            address = getattr(session_ctx, "chat_address", None)
            if address is not None and getattr(address, "is_typed", False):
                chat_address = address.chat_key
            else:
                session_id = session_ctx.session_id

        records: list[Any]
        if mode == "around_message":
            anchor = await service.find_turn_by_message_id(
                message_id,
                chat_address=chat_address,
                session_id=session_id,
            )
            if anchor is None:
                return []
            records = await service.read_turns_around(
                anchor.turn_id,
                chat_address=chat_address,
                session_id=session_id,
                before=before,
                after=after,
            )
        elif mode == "search":
            hits = await service.read_chat_turns(
                chat_address=chat_address,
                session_id=session_id,
                query=query,
                before_turn_id=before_turn_id,
                limit=limit,
            )
            if before <= 0 and after <= 0:
                records = hits
            else:
                expanded: dict[int, Any] = {}
                for hit in hits:
                    neighbors = await service.read_turns_around(
                        hit.turn_id,
                        chat_address=chat_address,
                        session_id=session_id,
                        before=before,
                        after=after,
                    )
                    expanded.update({record.turn_id: record for record in neighbors})
                records = sorted(
                    expanded.values(),
                    key=lambda record: (record.turn.created_at, record.turn_id),
                )
        else:
            records = await service.read_chat_turns(
                chat_address=chat_address,
                session_id=session_id,
                since=since,
                until=until,
                before_turn_id=before_turn_id,
                limit=limit,
            )

        return [self._chat_history_record(record) for record in records]

    @staticmethod
    def _chat_history_record(record: Any) -> dict[str, Any]:
        turn = record.turn
        metadata = turn.metadata if isinstance(turn.metadata, dict) else {}
        context = metadata.get("message_context")
        if not isinstance(context, dict):
            context = {}
        return {
            "turn_id": record.turn_id,
            "session_id": record.session_id,
            "role": turn.role,
            "source": turn.source,
            "content": turn.content,
            "created_at": turn.created_at.isoformat(),
            "message_id": str(
                metadata.get("message_id") or context.get("message_id") or ""
            ),
            "reply_to": str(
                metadata.get("reply_to") or context.get("reply_to_message_id") or ""
            ),
            "sender_id": str(context.get("sender_id") or ""),
            "sender_display_name": str(context.get("sender_display_name") or ""),
            "chat_type": str(context.get("chat_type") or ""),
            "chat_id": str(context.get("chat_id") or ""),
            "observed_only": metadata.get("observed_only") is True,
            "trigger_kind": str(metadata.get("trigger_kind") or ""),
        }

    async def search_chats(
        self, name: str, *, platform: str = ""
    ) -> list[dict[str, Any]]:
        """Fuzzy-search observed chat/group names → ChatAddress list.

        Backed by the chat_metadata table populated at the router. Observe-only
        (no live channel list API). Returns rows with chat_address / display_name
        / platform / last_seen_at. Empty list if no store is wired.
        """
        if self._chat_metadata_store is None or not name:
            return []
        search_by_name = getattr(self._chat_metadata_store, "search_by_name", None)
        if not callable(search_by_name):
            return []
        return await cast(Any, search_by_name)(name, platform=platform)

    async def get_chat_names(self, chat_keys: list[str]) -> dict[str, str]:
        """Bulk-resolve ``{chat_key: display_name}`` from observed chat metadata.

        Returns an empty map if no store is wired or no keys resolve.
        """
        if self._chat_metadata_store is None or not chat_keys:
            return {}
        get_many = getattr(self._chat_metadata_store, "get_many", None)
        if not callable(get_many):
            return {}
        return await cast(Any, get_many)(chat_keys)

    async def memory_store(
        self, key: str, content: str, *, metadata: dict[str, Any] | None = None
    ) -> str | None:
        self._permissions.check_memory_write()
        service = self._memory_service()
        if service is None:
            return
        from nahida_bot.core.context import current_session

        session_ctx = current_session.get()
        session_id = getattr(session_ctx, "session_id", "") if session_ctx else ""
        item_id = await service.store_item(
            key,
            content,
            ctx=session_ctx,
            session_id=session_id,
            metadata=metadata,
        )
        self._logger.debug(
            "memory_store_called", key=key, item_id=item_id, backend="items"
        )
        return item_id

    async def memory_update(
        self,
        item_id: str,
        content: str,
        *,
        key: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        self._permissions.check_memory_write()
        service = self._memory_service()
        if service is None:
            return None
        from nahida_bot.core.context import current_session

        session_ctx = current_session.get()
        session_id = getattr(session_ctx, "session_id", "") if session_ctx else ""
        replacement_id = await service.update_item_for_context(
            item_id,
            content,
            key=key,
            ctx=session_ctx,
            session_id=session_id,
            metadata=metadata,
        )
        self._logger.debug(
            "memory_update_called",
            item_id=item_id,
            replacement_id=replacement_id or "",
        )
        return replacement_id

    async def memory_archive(self, item_id: str) -> bool:
        self._permissions.check_memory_write()
        service = self._memory_service()
        if service is None:
            return False
        from nahida_bot.core.context import current_session

        session_ctx = current_session.get()
        session_id = getattr(session_ctx, "session_id", "") if session_ctx else ""
        archived = await service.archive_item_for_context(
            item_id,
            ctx=session_ctx,
            session_id=session_id,
        )
        self._logger.debug("memory_archive_called", item_id=item_id, archived=archived)
        return archived

    # ── Plugin Data Store ─────────────────────────────

    async def plugin_data_get(self, key: str) -> Any | None:
        """Read a value from this plugin's data store."""
        self._permissions.check_plugin_data_read()
        if self._plugin_data_repo is None:
            raise RuntimeError("Plugin data store is not available")
        return await self._plugin_data_repo.get(self._plugin_id, key)

    async def plugin_data_set(self, key: str, value: Any) -> None:
        """Write a value to this plugin's data store."""
        self._permissions.check_plugin_data_write()
        if self._plugin_data_repo is None:
            raise RuntimeError("Plugin data store is not available")
        await self._plugin_data_repo.set(self._plugin_id, key, value)

    async def plugin_data_delete(self, key: str) -> bool:
        """Delete a key from this plugin's data store."""
        self._permissions.check_plugin_data_write()
        if self._plugin_data_repo is None:
            raise RuntimeError("Plugin data store is not available")
        return await self._plugin_data_repo.delete(self._plugin_id, key)

    async def plugin_data_list(self, prefix: str = "") -> dict[str, Any]:
        """List key-value pairs, optionally filtered by key prefix."""
        self._permissions.check_plugin_data_read()
        if self._plugin_data_repo is None:
            raise RuntimeError("Plugin data store is not available")
        if prefix:
            return await self._plugin_data_repo.get_by_prefix(self._plugin_id, prefix)
        return await self._plugin_data_repo.get_all(self._plugin_id)

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

    def get_workspace_root(self, workspace_id: str | None = None) -> str | None:
        """Return the filesystem root path for a workspace.

        When *workspace_id* is ``None``, uses the active workspace.
        Returns ``None`` when the workspace manager is unavailable.
        """
        if self._workspace is None:
            return None
        if workspace_id is None:
            metadata = self._workspace.get_active_workspace()
            workspace_id = metadata.workspace_id
        return str(self._workspace.workspace_path(workspace_id))

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

    def get_active_session_id(self, address: ChatAddress) -> str:
        """Return the active session id for a typed chat address."""
        router = self._event_bus.context.app.message_router
        if router is None or not address.is_typed:
            return address.chat_key if address.is_typed else address.legacy_key
        return router.get_active_session_id(address)

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

    def get_model_router(self) -> Any:
        """Access the unified ModelRouter (if configured)."""
        return self._model_router

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
        for registration in self._webhook_registrations:
            self._activate_webhook_registration(registration)
        for type_key in self._registered_provider_types:
            self._activate_provider_type(type_key)
        for global_key in self._registered_supplements:
            self._activate_supplement(global_key)
        for global_key in self._registered_status_providers:
            self._activate_status_provider(global_key)

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
        self._deactivate_webhook_registrations(clear=False)

        from nahida_bot.agent.providers.registry import unregister_runtime_provider

        for type_key in list(self._active_provider_types):
            unregister_runtime_provider(type_key, owner_plugin_id=self._plugin_id)
            self._active_provider_types.discard(type_key)
        for global_key in list(self._active_supplements):
            if self._supplement_registry is not None:
                self._supplement_registry.unregister(global_key)
            self._active_supplements.discard(global_key)
        for global_key in list(self._active_status_providers):
            if self._status_provider_registry is not None:
                self._status_provider_registry.unregister(global_key)
            self._active_status_providers.discard(global_key)
        self._registrations_active = False

    def clear_registrations(self) -> None:
        """Permanently clear all registrations owned by this plugin."""
        self.deactivate_registrations()
        self._registered_commands.clear()
        self._registered_command_names.clear()
        self._registered_tools.clear()
        self._event_registrations.clear()
        self._registered_channels.clear()
        self._webhook_registrations.clear()
        self._registered_provider_types.clear()
        self._registered_supplements.clear()
        self._registered_status_providers.clear()
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

    def _activate_webhook_registration(
        self, registration: _WebhookRegistration
    ) -> None:
        if registration.active:
            return
        if self._webhost_service is None:
            raise RuntimeError("WebHost service is not available")
        registration.service_handle = self._webhost_service.register(
            plugin_id=self._plugin_id,
            path=registration.path,
            handler=registration.handler,
            methods=registration.methods,
        )
        registration.active = True

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

    def _remove_webhook_registration(self, registration: _WebhookRegistration) -> None:
        if registration.active and registration.service_handle is not None:
            registration.service_handle.unsubscribe()
        registration.service_handle = None
        registration.active = False
        if registration in self._webhook_registrations:
            self._webhook_registrations.remove(registration)

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

    def _deactivate_webhook_registrations(self, *, clear: bool) -> None:
        for registration in list(self._webhook_registrations):
            if registration.service_handle is not None:
                registration.service_handle.unsubscribe()
            registration.service_handle = None
            registration.active = False
        if clear:
            self._webhook_registrations.clear()

    # ── Cleanup ────────────────────────────────────────

    def clear_subscriptions(self) -> None:
        """Unsubscribe all event handlers registered by this plugin."""
        self._deactivate_event_registrations(clear=True)

    def set_runtime_services(
        self,
        *,
        workspace_manager: WorkspaceManager | None = None,
        memory_store: SQLiteMemoryStore | None = None,
        message_delivery_store: SQLiteMessageDeliveryStore | None = None,
        provider_manager: Any | None = None,
        model_router: Any | None = None,
        scheduler_service: Any | None = None,
        orchestration_service: Any | None = None,
        plugin_data_repo: SQLitePluginDataRepository | None = None,
        webhost_service: Any | None = None,
        task_manager: Any | None = None,
        document_store_manager: Any | None = None,
        chat_metadata_store: Any | None = None,
        temp_file_service: ManagedTempFileService | None = None,
    ) -> None:
        """Update runtime services after early plugin loading."""
        self._workspace = workspace_manager
        self._memory = memory_store
        # The lazily-built MemoryService wraps ``_memory``; a store swap (e.g.
        # the manager injecting the real store after early plugin load) would
        # leave a stale wrapper, so drop the cache on any reassignment.
        self._memory_service_cache = None
        self._message_delivery_store = message_delivery_store
        self._provider_manager = provider_manager
        self._model_router = model_router
        self._scheduler_service = scheduler_service
        self._orchestration_service = orchestration_service
        if chat_metadata_store is not None:
            self._chat_metadata_store = chat_metadata_store
        if webhost_service is not None:
            self._webhost_service = webhost_service
        if plugin_data_repo is not None:
            self._plugin_data_repo = plugin_data_repo
        if task_manager is not None:
            self._task_manager = task_manager
        if document_store_manager is not None:
            self._document_store_manager = document_store_manager
        if temp_file_service is not None:
            self._temp_file_service = temp_file_service

    # ── Document Store ────────────────────────────────────

    def get_document_store_manager(self) -> Any:
        """Return the ``DocumentStoreManager`` for creating/accessing document collections."""
        self._permissions.check_llm_access()
        return self._document_store_manager

    # ── Task Management ──────────────────────────────

    def spawn_task(
        self,
        name: str,
        coro: Coroutine[Any, Any, Any],
        *,
        kind: str = "oneshot",
    ) -> None:
        """Spawn a named background task owned by this plugin."""
        if self._task_manager is None:
            coro.close()
            raise RuntimeError("TaskManager is not available")
        self._task_manager.spawn(name=name, coro=coro, owner=self._plugin_id, kind=kind)

    def cancel_task(self, name: str) -> bool:
        """Cancel a task by name within this plugin's scope."""
        if self._task_manager is None:
            return False
        return self._task_manager.cancel(f"{self._plugin_id}:{name}")

    def spawn_interval_task(
        self,
        name: str,
        func: Callable[[], Awaitable[None]],
        *,
        interval_seconds: float,
        initial_delay: float = 0.0,
    ) -> None:
        """Spawn a periodic task owned by this plugin."""
        if self._task_manager is None:
            raise RuntimeError("TaskManager is not available")
        self._task_manager.spawn_interval(
            name=name,
            func=func,
            owner=self._plugin_id,
            interval_seconds=interval_seconds,
            initial_delay=initial_delay,
        )


def _address_from_inbound_message(message: InboundMessage) -> ChatAddress:
    """Build a typed chat address from a normalized inbound message."""
    chat_type = ""
    if message.chat_context and message.chat_context.chat_type:
        chat_type = message.chat_context.chat_type
    elif message.message_context and message.message_context.chat_type:
        chat_type = message.message_context.chat_type
    return ChatAddress.from_inbound(
        message.platform,
        message.chat_id,
        is_group=message.is_group,
        chat_type=chat_type,
    )
