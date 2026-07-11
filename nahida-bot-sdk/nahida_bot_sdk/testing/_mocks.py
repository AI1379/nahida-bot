"""Testing utilities for nahida-bot plugin development."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable, Coroutine
from uuid import uuid4

import structlog

from nahida_bot_sdk.api import ManagedTempFile
from nahida_bot_sdk.chat_address import ChatAddress
from nahida_bot_sdk.messaging import AttentionFrame, InboundMessage, OutboundMessage
from nahida_bot_sdk.plugin import bind_decorated_registrations

# A real structlog bound logger, same class family as production
# (``make_filtering_bound_logger``), silenced to CRITICAL. Replacing the old
# MagicMock here means reserved kwargs collide the same way they do in
# production: ``logger.info("x", event="y")`` raises TypeError at test time
# instead of 500ing in a prod webhook handler. Levels below CRITICAL are no-ops,
# so the mock produces no output.
_silent_stdlib_logger = logging.getLogger("nahida_bot_sdk.testing")
_silent_stdlib_logger.addHandler(logging.NullHandler())
_silent_stdlib_logger.propagate = False
_SILENT_LOG = structlog.wrap_logger(
    _silent_stdlib_logger,
    processors=[],
    wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
)


async def load_plugin_for_test(plugin: Any) -> None:
    """Register decorated handlers on a test BotAPI, then call ``on_load``."""
    bind_decorated_registrations(plugin)
    await plugin.on_load()


class MockBotAPI:
    """Minimal no-op BotAPI stub for testing.

    All methods are no-ops.  For stateful tracking (recording calls),
    use ``RecordingMockBotAPI`` instead.
    """

    async def send_message(
        self, target: str, message: Any, *, channel: str = ""
    ) -> str:
        return ""

    async def create_temp_file(
        self,
        *,
        suffix: str = "",
        prefix: str = "",
        purpose: str = "",
        ttl_seconds: int = 3600,
    ) -> ManagedTempFile:
        del purpose
        suffix = suffix if not suffix or suffix.startswith(".") else f".{suffix}"
        path = (
            Path(tempfile.gettempdir())
            / f"{prefix or 'nahida-test'}-{uuid4().hex}{suffix}"
        )
        path.touch(exist_ok=False)
        return ManagedTempFile(
            path=str(path),
            plugin_id="test",
            cleanup_token=uuid4().hex,
            ttl_seconds=ttl_seconds,
        )

    async def cleanup_temp_files(self, *, expired_only: bool = True) -> int:
        del expired_only
        return 0

    async def cleanup_temp_attachment(self, attachment: Any) -> bool:
        del attachment
        return False

    async def record_session_event(
        self,
        session_id: str,
        content: str,
        *,
        source: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        pass

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
        pass

    def on_event(self, event_type: type) -> Callable:
        return lambda f: f

    def subscribe(
        self, event_type: type, handler: Callable[..., Awaitable[None]]
    ) -> Any:
        return None

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Callable[..., Awaitable[str]],
    ) -> None:
        pass

    def unregister_tool(self, name: str) -> bool:
        return False

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

    def register_webhook_endpoint(
        self,
        path: str,
        handler: Callable[..., Awaitable[Any]],
        *,
        methods: tuple[str, ...] = ("POST",),
    ) -> Any:
        return _MockWebhookHandle()

    def register_prompt_supplement(
        self,
        key: str,
        instruction: str,
        *,
        channel: str | None = None,
        filter: Any | None = None,
    ) -> None:
        pass

    def unregister_prompt_supplement(self, key: str) -> bool:
        return False

    def register_command(
        self,
        name: str,
        handler: Callable[..., Awaitable[Any]],
        *,
        description: str = "",
        aliases: list[str] | None = None,
    ) -> None:
        pass

    async def get_session(self, session_id: str) -> Any:
        return None

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
        return ""

    async def clear_session(self, session_id: str) -> int:
        return 0

    async def start_new_session(self, address: ChatAddress) -> str | None:
        return None

    def get_active_session_id(self, address: ChatAddress) -> str:
        return address.chat_key

    async def get_session_info(self, session_id: str) -> dict[str, Any]:
        return {}

    def get_session_run_status(self, session_id: str) -> dict[str, Any]:
        return {"active": False, "state": "idle", "pending_messages": 0}

    def list_commands(self) -> list[Any]:
        return []

    def list_models(self) -> list[dict[str, str]]:
        return []

    async def set_session_model(self, session_id: str, model_name: str) -> str | None:
        return None

    async def llm_chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> Any:
        from nahida_bot_sdk.api import LLMResponse

        return LLMResponse(content="")

    async def run_subagent(
        self,
        prompt: str,
        *,
        model: str = "",
        system_prompt: str = "",
        tools: list[str] | None = None,
        max_steps: int = 10,
        timeout_seconds: int = 300,
    ) -> Any:
        from nahida_bot_sdk.api import SubagentResult

        return SubagentResult(final_response="")

    async def update_runtime_settings(
        self, session_id: str, updates: dict[str, Any]
    ) -> dict[str, Any]:
        return dict(updates)

    async def memory_search(self, query: str, *, limit: int = 5) -> list[Any]:
        return []

    async def identity_manage(
        self,
        action: str,
        *,
        person_id: str = "",
        display_name: str = "",
        account_key: str = "",
    ) -> dict[str, Any]:
        return {"action": action}

    @property
    def scheduler_service(self) -> Any | None:
        return None

    async def memory_store(
        self, key: str, content: str, *, metadata: dict[str, Any] | None = None
    ) -> str | None:
        return None

    async def memory_update(
        self,
        item_id: str,
        content: str,
        *,
        key: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        return None

    async def memory_archive(self, item_id: str) -> bool:
        return False

    # ── Plugin Data Store ─────────────────────────────

    async def plugin_data_get(self, key: str) -> Any | None:
        return None

    async def plugin_data_set(self, key: str, value: Any) -> None:
        pass

    async def plugin_data_delete(self, key: str) -> bool:
        return False

    async def plugin_data_list(self, prefix: str = "") -> dict[str, Any]:
        return {}

    async def workspace_read(self, path: str) -> str:
        return ""

    async def workspace_write(self, path: str, content: str) -> None:
        pass

    def resolve_workspace_path(self, path: str) -> str:
        return path

    async def publish_event(self, event: Any) -> None:
        pass

    @property
    def logger(self) -> Any:
        return _SILENT_LOG

    # ── Task Management ──────────────────────────────

    def spawn_task(
        self,
        name: str,
        coro: Coroutine[Any, Any, Any],
        *,
        kind: str = "oneshot",
    ) -> None:
        coro.close()

    def cancel_task(self, name: str) -> bool:
        return False

    def spawn_interval_task(
        self,
        name: str,
        func: Callable[[], Awaitable[None]],
        *,
        interval_seconds: float,
        initial_delay: float = 0.0,
    ) -> None:
        pass


class RecordingMockBotAPI(MockBotAPI):
    """Stateful BotAPI mock that records calls for assertion.

    Tracks: published events, registered tools, commands, handlers, services.
    """

    def __init__(self) -> None:
        self.published_events: list[Any] = []
        self.agent_response_requests: list[dict[str, Any]] = []
        self.registered_tools: dict[str, dict[str, Any]] = {}
        self.registered_commands: dict[str, dict[str, Any]] = {}
        self.registered_event_handlers: dict[
            type,
            list[Callable[..., Awaitable[None]]],
        ] = {}
        self.registered_channels: list[Any] = []
        self.registered_provider_types: dict[str, dict[str, Any]] = {}
        self.registered_webhooks: dict[str, dict[str, Any]] = {}
        self.registered_prompt_supplements: dict[str, dict[str, Any]] = {}
        self.spawned_tasks: dict[str, dict[str, Any]] = {}
        self._plugin_data: dict[str, Any] = {}

    def on_event(self, event_type: type) -> Callable:
        def decorator(handler: Callable[..., Awaitable[None]]) -> Callable:
            self.subscribe(event_type, handler)
            return handler

        return decorator

    def subscribe(
        self,
        event_type: type,
        handler: Callable[..., Awaitable[None]],
    ) -> Any:
        handlers = self.registered_event_handlers.setdefault(event_type, [])
        handlers.append(handler)
        return _MockSubHandle(handlers, handler)

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Any,
    ) -> None:
        if name in self.registered_tools:
            raise KeyError(f"Tool '{name}' is already registered")
        self.registered_tools[name] = {
            "description": description,
            "parameters": parameters,
            "handler": handler,
        }

    def unregister_tool(self, name: str) -> bool:
        return self.registered_tools.pop(name, None) is not None

    def register_command(
        self,
        name: str,
        handler: Callable[..., Awaitable[Any]],
        *,
        description: str = "",
        aliases: list[str] | None = None,
    ) -> None:
        names = [name, *(aliases or [])]
        seen: set[str] = set()
        for command_name in names:
            if command_name in seen:
                raise KeyError(f"Command '{command_name}' is duplicated")
            seen.add(command_name)
            if command_name in self.registered_commands:
                raise KeyError(f"Command '{command_name}' is already registered")
        entry = {
            "name": name,
            "handler": handler,
            "description": description,
            "aliases": aliases or [],
        }
        for command_name in names:
            self.registered_commands[command_name] = entry

    def register_channel(self, channel: Any) -> None:
        self.registered_channels.append(channel)

    def register_provider_type(
        self,
        type_key: str,
        factory: Any,
        *,
        config_schema: dict[str, Any] | None = None,
        description: str = "",
    ) -> None:
        if type_key in self.registered_provider_types:
            raise KeyError(f"Provider type '{type_key}' is already registered")
        self.registered_provider_types[type_key] = {
            "factory": factory,
            "config_schema": config_schema,
            "description": description,
        }

    def register_webhook_endpoint(
        self,
        path: str,
        handler: Callable[..., Awaitable[Any]],
        *,
        methods: tuple[str, ...] = ("POST",),
    ) -> Any:
        if path in self.registered_webhooks:
            raise KeyError(f"Webhook endpoint '{path}' is already registered")
        self.registered_webhooks[path] = {
            "handler": handler,
            "methods": tuple(methods),
        }

        def _unsubscribe() -> None:
            self.registered_webhooks.pop(path, None)

        return _MockWebhookHandle(_unsubscribe)

    def register_prompt_supplement(
        self,
        key: str,
        instruction: str,
        *,
        channel: str | None = None,
        filter: Any | None = None,
    ) -> None:
        if key in self.registered_prompt_supplements:
            raise KeyError(f"Prompt supplement '{key}' is already registered")
        self.registered_prompt_supplements[key] = {
            "instruction": instruction,
            "channel": channel,
            "filter": filter,
        }

    def unregister_prompt_supplement(self, key: str) -> bool:
        return self.registered_prompt_supplements.pop(key, None) is not None

    async def publish_event(self, event: Any) -> None:
        self.published_events.append(event)

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
        self.agent_response_requests.append(
            {
                "message": message,
                "session_id": session_id,
                "reason": reason,
                "instruction": instruction,
                "observed_messages": observed_messages,
                "reply_to_message_id": reply_to_message_id,
                "attention_frame": attention_frame,
            }
        )

    # ── Plugin Data Store (in-memory) ─────────────────

    async def plugin_data_get(self, key: str) -> Any | None:
        return self._plugin_data.get(key)

    async def plugin_data_set(self, key: str, value: Any) -> None:
        self._plugin_data[key] = value

    async def plugin_data_delete(self, key: str) -> bool:
        return self._plugin_data.pop(key, _SENTINEL) is not _SENTINEL

    async def plugin_data_list(self, prefix: str = "") -> dict[str, Any]:
        if prefix:
            return {k: v for k, v in self._plugin_data.items() if k.startswith(prefix)}
        return dict(self._plugin_data)

    def register_status_provider(
        self,
        key: str,
        handler: Any,
        *,
        label: str = "",
    ) -> None:
        pass

    def unregister_status_provider(self, key: str) -> bool:
        return False

    async def collect_status_providers(
        self,
        *,
        session_id: str,
        chat_key: str,
    ) -> list[str]:
        return []

    # ── Task Management ──────────────────────────────

    def spawn_task(
        self,
        name: str,
        coro: Coroutine[Any, Any, Any],
        *,
        kind: str = "oneshot",
    ) -> None:
        self.spawned_tasks[name] = {"kind": kind}
        coro.close()

    def cancel_task(self, name: str) -> bool:
        return self.spawned_tasks.pop(name, None) is not None

    def spawn_interval_task(
        self,
        name: str,
        func: Callable[[], Awaitable[None]],
        *,
        interval_seconds: float,
        initial_delay: float = 0.0,
    ) -> None:
        self.spawned_tasks[name] = {
            "kind": "interval",
            "func": func,
            "interval_seconds": interval_seconds,
            "initial_delay": initial_delay,
        }


_SENTINEL = object()


class StubChannelService:
    """Plain object satisfying the ChannelService protocol (not a Plugin).

    Use when you only need a channel-shaped object without Plugin machinery.
    For tests that need a real Plugin, extend Plugin directly in the test.
    """

    def __init__(self, channel_id: str = "test.channel") -> None:
        self._channel_id = channel_id

    @property
    def channel_id(self) -> str:
        return self._channel_id

    async def handle_inbound_event(self, event: dict[str, Any]) -> None:
        pass

    async def send_message(
        self,
        target: str,
        message: Any,  # OutboundMessage
    ) -> str:
        return "msg_1"


class ConsoleMockBotAPI:
    """Interactive-capable BotAPI mock for the plugin testing console.

    Tracks all registrations and supports dispatching commands, tools,
    and events interactively.  Also records sent messages so the console
    can print responses.
    """

    def __init__(self) -> None:
        self.sent_messages: list[tuple[str, OutboundMessage]] = []
        self.agent_response_requests: list[dict[str, Any]] = []
        self._tools: dict[str, dict[str, Any]] = {}
        self._commands: dict[str, dict[str, Any]] = {}
        self._event_handlers: dict[type, list[Callable[..., Awaitable[None]]]] = {}
        self._channels: list[Any] = []
        self._provider_types: dict[str, dict[str, Any]] = {}
        self._workspace: dict[str, str] = {}
        self.spawned_tasks: dict[str, dict[str, Any]] = {}

    # ── Messaging ──────────────────────────────────────

    async def send_message(
        self, target: str, message: OutboundMessage, *, channel: str = ""
    ) -> str:
        self.sent_messages.append((target, message))
        return f"msg_{len(self.sent_messages)}"

    async def record_session_event(
        self,
        session_id: str,
        content: str,
        *,
        source: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        pass

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
        self.agent_response_requests.append(
            {
                "message": message,
                "session_id": session_id,
                "reason": reason,
                "instruction": instruction,
                "observed_messages": observed_messages,
                "reply_to_message_id": reply_to_message_id,
                "attention_frame": attention_frame,
            }
        )

    async def record_message_delivery(self, **kw: Any) -> str:
        return ""

    # ── Event System ───────────────────────────────────

    def on_event(self, event_type: type) -> Callable:
        def decorator(handler: Callable[..., Awaitable[None]]) -> Callable:
            self.subscribe(event_type, handler)
            return handler

        return decorator

    def subscribe(
        self,
        event_type: type,
        handler: Callable[..., Awaitable[None]],
    ) -> _MockSubHandle:
        handlers = self._event_handlers.setdefault(event_type, [])
        handlers.append(handler)
        return _MockSubHandle(handlers, handler)

    async def _trigger_event(self, event: Any) -> None:
        """Fire an event to all registered handlers for that event type."""
        for handler in self._event_handlers.get(type(event), []):
            await handler(event)

    # ── Tool Registration ──────────────────────────────

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Callable[..., Awaitable[str]],
    ) -> None:
        if name in self._tools:
            raise KeyError(f"Tool '{name}' is already registered")
        self._tools[name] = {
            "description": description,
            "parameters": parameters,
            "handler": handler,
        }

    def unregister_tool(self, name: str) -> bool:
        return self._tools.pop(name, None) is not None

    def list_tools(self) -> list[str]:
        return list(self._tools)

    async def invoke_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Invoke a registered tool by name with JSON-decoded arguments."""
        entry = self._tools.get(name)
        if entry is None:
            return (
                f"[Error] Tool '{name}' not found. Available: {', '.join(self._tools)}"
            )
        try:
            return await entry["handler"](**arguments)
        except Exception as exc:
            return f"[Error] Tool '{name}' raised {type(exc).__name__}: {exc}"

    # ── Command Registration ───────────────────────────

    def register_command(
        self,
        name: str,
        handler: Callable[..., Awaitable[Any]],
        *,
        description: str = "",
        aliases: list[str] | None = None,
    ) -> None:
        names = [name, *(aliases or [])]
        seen: set[str] = set()
        for command_name in names:
            if command_name in seen:
                raise KeyError(f"Command '{command_name}' is duplicated")
            seen.add(command_name)
            if command_name in self._commands:
                raise KeyError(f"Command '{command_name}' is already registered")
        entry = {
            "handler": handler,
            "description": description,
            "aliases": aliases or [],
            "name": name,
        }
        for command_name in names:
            self._commands[command_name] = entry

    def list_commands(self) -> list[dict[str, Any]]:
        seen: set[int] = set()
        result: list[dict[str, Any]] = []
        for _name, entry in self._commands.items():
            hid = id(entry)
            if hid not in seen:
                seen.add(hid)
                result.append(
                    {
                        "name": entry["name"],
                        "description": entry["description"],
                        "aliases": entry["aliases"],
                    }
                )
        return result

    async def invoke_command(self, name: str, args: str = "") -> str:
        """Dispatch a command by name and return the result text."""
        entry = self._commands.get(name)
        if entry is None:
            return f"[Error] Command '{name}' not found."
        handler = entry["handler"]
        inbound = InboundMessage(
            message_id="console_0",
            platform="console",
            chat_id="test",
            user_id="console_user",
            text=f"/{name} {args}".rstrip(),
            raw_event={},
        )
        try:
            result = await handler(
                args=args,
                inbound=inbound,
                session_id="console:private:test",
            )
        except Exception as exc:
            return f"[Error] Command '{name}' raised {type(exc).__name__}: {exc}"

        return _format_command_result(result)

    # ── Service Registration ──────────────────────────

    def register_channel(self, channel: Any) -> None:
        self._channels.append(channel)

    def register_provider_type(
        self,
        type_key: str,
        factory: Any,
        *,
        config_schema: dict[str, Any] | None = None,
        description: str = "",
    ) -> None:
        if type_key in self._provider_types:
            raise KeyError(f"Provider type '{type_key}' is already registered")
        self._provider_types[type_key] = {
            "factory": factory,
            "config_schema": config_schema,
            "description": description,
        }

    def register_webhook_endpoint(
        self,
        path: str,
        handler: Callable[..., Awaitable[Any]],
        *,
        methods: tuple[str, ...] = ("POST",),
    ) -> Any:
        return _MockWebhookHandle()

    def register_prompt_supplement(
        self,
        key: str,
        instruction: str,
        *,
        channel: str | None = None,
        filter: Any | None = None,
    ) -> None:
        pass

    def unregister_prompt_supplement(self, key: str) -> bool:
        return False

    @property
    def scheduler_service(self) -> Any | None:
        return None

    # ── Session ────────────────────────────────────────

    async def get_session(self, session_id: str) -> Any:
        return None

    async def clear_session(self, session_id: str) -> int:
        return 0

    async def start_new_session(self, address: ChatAddress) -> str | None:
        return "console:private:test:new"

    def get_active_session_id(self, address: ChatAddress) -> str:
        return address.chat_key

    async def get_session_info(self, session_id: str) -> dict[str, Any]:
        return {}

    def get_session_run_status(self, session_id: str) -> dict[str, Any]:
        return {"active": False, "state": "idle", "pending_messages": 0}

    def list_models(self) -> list[dict[str, str]]:
        return []

    async def set_session_model(self, session_id: str, model_name: str) -> str | None:
        return None

    async def llm_chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> Any:
        from nahida_bot_sdk.api import LLMResponse

        return LLMResponse(content="")

    async def run_subagent(
        self,
        prompt: str,
        *,
        model: str = "",
        system_prompt: str = "",
        tools: list[str] | None = None,
        max_steps: int = 10,
        timeout_seconds: int = 300,
    ) -> Any:
        from nahida_bot_sdk.api import SubagentResult

        return SubagentResult(final_response="")

    async def update_runtime_settings(
        self, session_id: str, updates: dict[str, Any]
    ) -> dict[str, Any]:
        return dict(updates)

    # ── Memory ─────────────────────────────────────────

    async def memory_search(self, query: str, *, limit: int = 5) -> list[Any]:
        return []

    async def identity_manage(
        self,
        action: str,
        *,
        person_id: str = "",
        display_name: str = "",
        account_key: str = "",
    ) -> dict[str, Any]:
        return {"action": action}

    async def memory_store(
        self, key: str, content: str, *, metadata: dict[str, Any] | None = None
    ) -> str | None:
        return None

    async def memory_update(
        self,
        item_id: str,
        content: str,
        *,
        key: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        return None

    async def memory_archive(self, item_id: str) -> bool:
        return False

    # ── Plugin Data Store ─────────────────────────────

    async def plugin_data_get(self, key: str) -> Any | None:
        return None

    async def plugin_data_set(self, key: str, value: Any) -> None:
        pass

    async def plugin_data_delete(self, key: str) -> bool:
        return False

    async def plugin_data_list(self, prefix: str = "") -> dict[str, Any]:
        return {}

    # ── Workspace ──────────────────────────────────────

    async def workspace_read(self, path: str) -> str:
        return self._workspace.get(path, "")

    async def workspace_write(self, path: str, content: str) -> None:
        self._workspace[path] = content

    def resolve_workspace_path(self, path: str) -> str:
        return path

    # ── Event Publishing ───────────────────────────────

    async def publish_event(self, event: Any) -> None:
        pass

    @property
    def logger(self) -> Any:
        return _SILENT_LOG

    # ── Task Management ──────────────────────────────

    def spawn_task(
        self,
        name: str,
        coro: Coroutine[Any, Any, Any],
        *,
        kind: str = "oneshot",
    ) -> None:
        self.spawned_tasks[name] = {"kind": kind}
        coro.close()

    def cancel_task(self, name: str) -> bool:
        return self.spawned_tasks.pop(name, None) is not None

    def spawn_interval_task(
        self,
        name: str,
        func: Callable[[], Awaitable[None]],
        *,
        interval_seconds: float,
        initial_delay: float = 0.0,
    ) -> None:
        self.spawned_tasks[name] = {
            "kind": "interval",
            "func": func,
            "interval_seconds": interval_seconds,
            "initial_delay": initial_delay,
        }

    # ── Console helpers ───────────────────────────────

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools)

    @property
    def event_handler_types(self) -> list[type]:
        return list(self._event_handlers)

    @property
    def channel_count(self) -> int:
        return len(self._channels)


class _MockSubHandle:
    def __init__(
        self,
        handlers: list[Callable[..., Awaitable[None]]],
        handler: Callable[..., Awaitable[None]],
    ) -> None:
        self._handlers = handlers
        self._handler = handler

    def unsubscribe(self) -> None:
        if self._handler in self._handlers:
            self._handlers.remove(self._handler)


class _MockWebhookHandle:
    def __init__(self, unsubscribe: Callable[[], None] | None = None) -> None:
        self._unsubscribe = unsubscribe
        self.unsubscribed = False

    def unsubscribe(self) -> None:
        if self.unsubscribed:
            return
        self.unsubscribed = True
        if self._unsubscribe is not None:
            self._unsubscribe()


def _format_command_result(result: Any) -> str:
    """Convert a CommandHandlerResult to a display string."""
    if result is None:
        return "(no response)"
    if isinstance(result, str):
        return result

    from nahida_bot_sdk.commands import CommandResult

    if isinstance(result, CommandResult):
        if result.suppress_response:
            return "(suppressed)"
        if isinstance(result.message, OutboundMessage):
            return result.message.text
        if isinstance(result.message, str):
            return result.message
        return "(empty result)"
    if isinstance(result, OutboundMessage):
        return result.text
    return str(result)
