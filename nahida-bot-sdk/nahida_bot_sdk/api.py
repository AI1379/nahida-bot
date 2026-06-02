"""BotAPI protocol, ChannelService protocol, and related interfaces."""

from __future__ import annotations

from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Protocol,
    runtime_checkable,
)

from nahida_bot_sdk.chat_address import ChatAddress
from nahida_bot_sdk.messaging import OutboundMessage

if TYPE_CHECKING:
    from nahida_bot_sdk.commands import CommandHandlerResult, CommandInfo
    from nahida_bot_sdk.plugin import MemoryRef, SessionInfo


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
    ) -> None:
        """Persist a record to the memory store."""
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

    # ── Event Publishing ───────────────────────────────

    async def publish_event(self, event: Any) -> None:
        """Publish an event on the event bus."""
        ...

    # ── Logging ────────────────────────────────────────

    @property
    def logger(self) -> PluginLogger:
        """Structured logger scoped to this plugin."""
        ...
