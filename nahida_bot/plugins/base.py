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


@dataclass(slots=True, frozen=True)
class OutboundMessage:
    """Normalized message to send to an external platform."""

    text: str
    reply_to: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


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

    # ── Session ────────────────────────────────────────

    async def get_session(self, session_id: str) -> SessionInfo | None:
        """Look up session metadata."""
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
