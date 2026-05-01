"""Plugin base class, BotAPI protocol, and message types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Protocol,
    runtime_checkable,
)

if TYPE_CHECKING:
    from nahida_bot.plugins.commands import CommandHandlerResult, CommandInfo
    from nahida_bot.plugins.manifest import PluginManifest


# ── Message Types ──────────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class InboundMessage:
    """Normalized message received from an external platform."""

    message_id: str
    platform: str  # e.g. "telegram", "qq"
    chat_id: str
    user_id: str
    text: str
    raw_event: dict[str, Any]
    is_group: bool = False
    reply_to: str = ""
    timestamp: float = 0.0
    command_prefix: str = "/"  # Prefix used for command matching on this platform


@dataclass(slots=True, frozen=True)
class MediaDownloadResult:
    """Result of downloading a media file from a platform."""

    path: str  # local file path where the file was saved
    file_name: str = ""
    mime_type: str = ""
    file_size: int = 0


@dataclass(slots=True, frozen=True)
class Attachment:
    """A file attachment for an outbound message."""

    type: str  # "photo", "document", "audio", "video"
    path: str  # local file path
    filename: str = ""
    mime_type: str = ""
    caption: str = ""


@dataclass(slots=True, frozen=True)
class OutboundMessage:
    """Normalized message to send to an external platform."""

    text: str
    reply_to: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    attachments: list[Attachment] = field(default_factory=list)


# ── Logger Protocol ────────────────────────────────────────────


class PluginLogger(Protocol):
    """Structured logger automatically scoped to a plugin."""

    def debug(self, msg: str, **kwargs: object) -> None: ...
    def info(self, msg: str, **kwargs: object) -> None: ...
    def warning(self, msg: str, **kwargs: object) -> None: ...
    def error(self, msg: str, **kwargs: object) -> None: ...
    def exception(self, msg: str, **kwargs: object) -> None: ...


# ── Subscription Handle ───────────────────────────────────────


class SubscriptionHandle(Protocol):
    """Handle returned by subscribe(); call unsubscribe() to detach."""

    def unsubscribe(self) -> None: ...


# ── Session Info ──────────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class SessionInfo:
    """Snapshot of an active session."""

    session_id: str
    channel: str
    chat_id: str
    user_id: str
    workspace_id: str = ""


# ── Memory Reference ──────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class MemoryRef:
    """A retrieved memory record."""

    key: str
    content: str
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


# ── BotAPI Protocol ───────────────────────────────────────────


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

    async def start_new_session(self, platform: str, chat_id: str) -> str | None:
        """Switch the active chat to a new session and return its id."""
        ...

    async def get_session_info(self, session_id: str) -> dict[str, Any]:
        """Return command-facing session metadata."""
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

    # ── Event Publishing ───────────────────────────────

    async def publish_event(self, event: Any) -> None:
        """Publish an event on the event bus."""
        ...

    # ── Logging ────────────────────────────────────────

    @property
    def logger(self) -> PluginLogger:
        """Structured logger scoped to this plugin."""
        ...


# ── Plugin Base Class ─────────────────────────────────────────


class Plugin(ABC):
    """Base class for all nahida-bot plugins.

    Subclass and implement ``on_load`` to register event handlers and tools.
    Optionally override ``on_unload``, ``on_enable``, and ``on_disable``
    for lifecycle management.
    """

    def __init__(self, api: BotAPI, manifest: PluginManifest) -> None:
        self._api = api
        self._manifest = manifest

    @property
    def api(self) -> BotAPI:
        """Bot capabilities available to this plugin."""
        return self._api

    @property
    def manifest(self) -> PluginManifest:
        """This plugin's manifest metadata."""
        return self._manifest

    @abstractmethod
    async def on_load(self) -> None:
        """Called when the plugin is loaded. Register handlers/tools here."""
        ...

    async def on_unload(self) -> None:
        """Called when the plugin is being unloaded. Clean up resources."""
        pass

    async def on_enable(self) -> None:
        """Called when the plugin is enabled after loading."""
        pass

    async def on_disable(self) -> None:
        """Called when the plugin is being disabled."""
        pass
