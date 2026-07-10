"""BotAPI protocol, ChannelService protocol, and related interfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Coroutine,
    Protocol,
    runtime_checkable,
)

from nahida_bot_sdk.chat_address import ChatAddress
from nahida_bot_sdk.messaging import (
    Attachment,
    AttentionFrame,
    InboundMessage,
    MessageContext,
    OutboundMessage,
)

if TYPE_CHECKING:
    from nahida_bot_sdk.commands import CommandHandlerResult, CommandInfo
    from nahida_bot_sdk.plugin import MemoryRef, SessionInfo


# ── LLM / Subagent data types ──────────────────────────


@dataclass(slots=True)
class LLMUsage:
    """Token usage from an LLM call."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass(slots=True)
class LLMResponse:
    """Normalized response from a single-turn LLM chat call."""

    content: str
    model: str = ""
    provider: str = ""
    finish_reason: str = ""
    usage: LLMUsage | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class SubagentResult:
    """Result from a multi-turn subagent run."""

    final_response: str
    status: str = "succeeded"  # succeeded | failed | timed_out | cancelled
    model: str = ""
    provider: str = ""
    steps: int = 0
    usage: LLMUsage | None = None
    error: str = ""


class PluginLogger(Protocol):
    """Structured logger automatically scoped to a plugin."""

    def debug(self, msg: str, **kwargs: object) -> None: ...
    def info(self, msg: str, **kwargs: object) -> None: ...
    def warning(self, msg: str, **kwargs: object) -> None: ...
    def error(self, msg: str, **kwargs: object) -> None: ...
    def exception(self, msg: str, **kwargs: object) -> None: ...


class SubscriptionHandle(Protocol):
    """Handle returned by subscribe(); call unsubscribe() to detach."""

    def unsubscribe(self) -> None: ...


class WebhookHandle(Protocol):
    """Handle returned by register_webhook_endpoint()."""

    def unsubscribe(self) -> None: ...


@dataclass(slots=True, frozen=True)
class WebhookRequest:
    """Raw HTTP request delivered to a plugin-owned webhook endpoint."""

    method: str
    path: str
    headers: dict[str, str]
    query: dict[str, str]
    body: bytes
    client_host: str = ""


@dataclass(slots=True, frozen=True)
class WebhookResponse:
    """Raw HTTP response returned by a plugin-owned webhook endpoint."""

    status_code: int = 204
    body: bytes | str = b""
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class ManagedTempFile:
    """A plugin-owned temporary file path managed by the bot runtime."""

    path: str
    plugin_id: str = ""
    cleanup_token: str = ""
    ttl_seconds: int = 3600

    def as_attachment(
        self,
        *,
        type: str,
        filename: str = "",
        mime_type: str = "",
        caption: str = "",
        cleanup_after_send: bool = True,
    ) -> Attachment:
        """Create an outbound attachment bound to this managed temporary file."""
        extra = {
            "managed_temp_file": True,
            "cleanup_token": self.cleanup_token,
            "cleanup_after_send": cleanup_after_send,
        }
        return Attachment(
            type=type,
            path=self.path,
            filename=filename,
            mime_type=mime_type,
            caption=caption,
            extra=extra,
        )


@runtime_checkable
class ChannelService(Protocol):
    """Runtime contract for a channel service exposed by a plugin.

    Channel services are ordinary plugins that explicitly register themselves
    with ``BotAPI.register_channel()``.
    """

    @property
    def channel_id(self) -> str:
        """Unique channel/platform identifier."""
        ...

    async def handle_inbound_event(self, event: dict[str, Any]) -> None:
        """Normalize one platform-native event and publish a bot event."""
        ...

    async def send_message(self, target: str, message: OutboundMessage) -> str:
        """Send one normalized outbound message to the channel."""
        ...


@runtime_checkable
class BotAPI(Protocol):
    """Interface that plugins use to interact with the bot runtime.

    The concrete implementation is injected at load time; tests inject a mock.
    """

    # ── Messaging ──────────────────────────────────────

    async def send_message(
        self, target: str, message: OutboundMessage, *, channel: str = ""
    ) -> str:
        """Send a message to an external target. Returns platform message ID."""
        ...

    async def record_session_event(
        self,
        session_id: str,
        content: str,
        *,
        source: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Write a system turn into a session's history without triggering a run."""
        ...

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
        """Ask the main router to run the agent for a group conversation."""
        ...

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
        """Write an outbound delivery audit record without affecting memory."""
        ...

    async def create_temp_file(
        self,
        *,
        suffix: str = "",
        prefix: str = "",
        purpose: str = "",
        ttl_seconds: int = 3600,
    ) -> ManagedTempFile:
        """Allocate a plugin-scoped temporary file managed by the bot runtime."""
        ...

    async def cleanup_temp_files(self, *, expired_only: bool = True) -> int:
        """Clean this plugin's managed temporary files."""
        ...

    async def cleanup_temp_attachment(self, attachment: Attachment) -> bool:
        """Clean one managed temporary attachment."""
        ...

    # ── Event System ───────────────────────────────────

    def on_event(self, event_type: type) -> Callable:
        """Decorator: register an event handler."""
        ...

    def subscribe(
        self,
        event_type: type,
        handler: Callable[..., Awaitable[None]],
    ) -> SubscriptionHandle:
        """Programmatic event subscription. Returns an unsubscribe handle."""
        ...

    # ── Tool Registration ──────────────────────────────

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],  # JSON Schema
        handler: Callable[..., Awaitable[str]],
    ) -> None:
        """Register a tool that the LLM can call during conversations."""
        ...

    def unregister_tool(self, name: str) -> bool:
        """Remove a previously registered tool by name. Returns ``True`` if found."""
        ...

    # ── Service Registration ──────────────────────────

    def register_channel(self, channel: ChannelService) -> None:
        """Register a channel service implemented by this plugin."""
        ...

    def register_provider_type(
        self,
        type_key: str,
        factory: Callable[[dict[str, Any]], Any],
        *,
        config_schema: dict[str, Any] | None = None,
        description: str = "",
    ) -> None:
        """Register a provider type that can be used from YAML config."""
        ...

    def register_webhook_endpoint(
        self,
        path: str,
        handler: Callable[[WebhookRequest], Awaitable[WebhookResponse | None]],
        *,
        methods: tuple[str, ...] = ("POST",),
    ) -> WebhookHandle:
        """Register a plugin-owned raw HTTP webhook endpoint."""
        ...

    def register_prompt_supplement(
        self,
        key: str,
        instruction: str,
        *,
        channel: str | None = None,
        filter: Callable[[MessageContext], bool] | None = None,
    ) -> None:
        """Register a supplemental instruction to inject into the system prompt.

        The supplement is appended after the base prompt and behavioral
        instructions.  When *channel* is provided, the supplement is only
        injected for messages originating from that channel.  When *filter*
        is provided, it is called with the current ``MessageContext``.
        When both are given, both must match (AND logic).  When neither is
        provided, the supplement is always injected.

        Args:
            key: Unique identifier for this supplement within the plugin.
            instruction: The prompt text to inject.
            channel: Optional channel name to restrict injection to.
            filter: Optional callable for complex matching conditions.
        """
        ...

    def unregister_prompt_supplement(self, key: str) -> bool:
        """Remove a previously registered prompt supplement. Returns ``True`` if found."""
        ...

    # ── Status Provider Registration ──────────────────

    def register_status_provider(
        self,
        key: str,
        handler: Callable[..., Awaitable[str | None]],
        *,
        label: str = "",
    ) -> None:
        """Register a provider that contributes text to ``/status`` output.

        The *handler* is an async callable invoked with keyword arguments
        ``session_id`` and ``chat_key``.  It should return a short text
        block describing the plugin's state for that chat, or ``None`` to
        contribute nothing.
        """
        ...

    def unregister_status_provider(self, key: str) -> bool:
        """Remove a previously registered status provider. Returns ``True`` if found."""
        ...

    async def collect_status_providers(
        self,
        *,
        session_id: str,
        chat_key: str,
    ) -> list[str]:
        """Collect text blocks from all registered status providers."""
        ...

    @property
    def scheduler_service(self) -> Any | None:
        """Scheduler service exposed to plugins that provide scheduler tools."""
        ...

    # ── Command Registration ───────────────────────────

    def register_command(
        self,
        name: str,
        handler: Callable[..., Awaitable[CommandHandlerResult]],
        *,
        description: str = "",
        aliases: list[str] | None = None,
    ) -> None:
        """Register a /command that is matched from incoming messages."""
        ...

    # ── Session ────────────────────────────────────────

    async def get_session(self, session_id: str) -> SessionInfo | None:
        """Look up session metadata."""
        ...

    async def clear_session(self, session_id: str) -> int:
        """Delete all turns for a session and return the number removed."""
        ...

    async def start_new_session(self, address: ChatAddress) -> str | None:
        """Switch the active chat to a new session and return its id."""
        ...

    def get_active_session_id(self, address: ChatAddress) -> str:
        """Return the current active session id for a chat address."""
        ...

    async def get_session_info(self, session_id: str) -> dict[str, Any]:
        """Return command-facing session metadata."""
        ...

    def get_session_run_status(self, session_id: str) -> dict[str, Any]:
        """Return command-facing agent run status for a session."""
        ...

    def list_commands(self) -> list[CommandInfo]:
        """List registered commands."""
        ...

    def list_models(self) -> list[dict[str, str]]:
        """List available provider/model pairs."""
        ...

    async def set_session_model(self, session_id: str, model_name: str) -> str | None:
        """Switch the session to a model and return provider id if found."""
        ...

    # ── LLM Access ──────────────────────────────────────

    async def llm_chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Send a single-turn chat request to an LLM.

        The *model* spec is resolved via the bot's built-in ModelRouter:
        it accepts tags (``"cheap"``), bare model names, or
        ``provider/model`` compound form. An empty string uses the
        default provider's default model.

        Tools are passed through to the provider but NOT executed —
        the plugin receives ``tool_calls`` in the response and handles
        them itself.
        """
        ...

    async def run_subagent(
        self,
        prompt: str,
        *,
        model: str = "",
        system_prompt: str = "",
        tools: list[str] | None = None,
        max_steps: int = 10,
        timeout_seconds: int = 300,
    ) -> SubagentResult:
        """Run a multi-turn subagent with optional tool access.

        The subagent runs in an isolated child session. *tools* is a
        list of tool names to grant (empty = no tools). The subagent
        can call any of them during its reasoning loop.

        Requires an active session context (e.g. inside a command or
        event handler).
        """
        ...

    async def update_runtime_settings(
        self, session_id: str, updates: dict[str, Any]
    ) -> dict[str, Any]:
        """Merge runtime settings into session metadata and return the result."""
        ...

    # ── Memory ─────────────────────────────────────────

    async def memory_search(self, query: str, *, limit: int = 5) -> list[MemoryRef]:
        """Search the memory store for relevant records."""
        ...

    async def memory_store(
        self, key: str, content: str, *, metadata: dict[str, Any] | None = None
    ) -> str | None:
        """Persist a record to the memory store and return its item id."""
        ...

    async def memory_update(
        self,
        item_id: str,
        content: str,
        *,
        key: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        """Replace a visible memory item and return the replacement id."""
        ...

    async def memory_archive(self, item_id: str) -> bool:
        """Archive a visible memory item."""
        ...

    async def search_chat_history(
        self,
        query: str,
        *,
        chat_address: str = "",
        role: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search raw conversation turns across ALL sessions (soft-gated).

        Returns dicts with ``session_id`` / ``role`` / ``content`` / ``created_at``.
        Soft-gated (no permission check, no scope restriction) — memory is soft
        context; the gating lives in the calling tool's description.
        """
        ...

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
        """Read structured chronological chat history for explicit recall."""
        ...

    async def search_chats(
        self, name: str, *, platform: str = ""
    ) -> list[dict[str, Any]]:
        """Fuzzy-search observed chat/group names → rows with ``chat_address``.

        Returns dicts with ``chat_address`` / ``display_name`` / ``platform`` /
        ``last_seen_at``. Only knows chats the bot has seen (observe-only).
        """
        ...

    async def get_chat_names(self, chat_keys: list[str]) -> dict[str, str]:
        """Bulk-resolve ``{chat_key: display_name}`` for observed chats.

        Unseen keys are absent from the returned map. Empty map if unavailable.
        """
        ...

    # ── Plugin Data Store ─────────────────────────────

    async def plugin_data_get(self, key: str) -> Any | None:
        """Read a value from this plugin's data store. Returns parsed JSON or ``None``."""
        ...

    async def plugin_data_set(self, key: str, value: Any) -> None:
        """Write a value to this plugin's data store. Overwrites if the key exists."""
        ...

    async def plugin_data_delete(self, key: str) -> bool:
        """Delete a key from this plugin's data store. Returns ``True`` if it existed."""
        ...

    async def plugin_data_list(self, prefix: str = "") -> dict[str, Any]:
        """List key-value pairs, optionally filtered by key prefix."""
        ...

    # ── Workspace ──────────────────────────────────────

    async def workspace_read(self, path: str) -> str:
        """Read a file from the workspace. Subject to permission checks."""
        ...

    async def workspace_write(self, path: str, content: str) -> None:
        """Write a file to the workspace. Subject to permission checks."""
        ...

    def resolve_workspace_path(self, path: str) -> str:
        """Resolve a workspace-relative path to an absolute local path."""
        ...

    def get_workspace_root(self, workspace_id: str | None = None) -> str | None:
        """Return the filesystem root path for a workspace.

        When *workspace_id* is ``None``, uses the active workspace.
        Returns ``None`` when the workspace manager is unavailable.
        """
        ...
        ...

    # ── Event Publishing ───────────────────────────────

    async def publish_event(self, event: Any) -> None:
        """Publish an event on the event bus."""
        ...

    # ── Logging ────────────────────────────────────────

    @property
    def logger(self) -> PluginLogger:
        """Structured logger scoped to this plugin."""
        ...

    # ── Task Management ──────────────────────────────

    def spawn_task(
        self,
        name: str,
        coro: Coroutine[Any, Any, Any],
        *,
        kind: str = "oneshot",
    ) -> None:
        """Spawn a named background task owned by this plugin.

        The task is automatically cancelled when the plugin is disabled.
        The coroutine runs in the bot's event loop.  Uncaught exceptions
        are logged automatically.
        """
        ...

    def cancel_task(self, name: str) -> bool:
        """Cancel a previously spawned task by name.  Returns ``True`` if found."""
        ...

    def spawn_interval_task(
        self,
        name: str,
        func: Callable[[], Awaitable[None]],
        *,
        interval_seconds: float,
        initial_delay: float = 0.0,
    ) -> None:
        """Spawn a periodic task owned by this plugin."""
        ...

    # ── Document Store ────────────────────────────────────

    def get_document_store_manager(self) -> Any:
        """Return the ``DocumentStoreManager`` for creating/accessing document collections.

        Returns ``None`` if the document store subsystem is not available.
        Requires the ``llm_access`` permission.
        """
        ...
