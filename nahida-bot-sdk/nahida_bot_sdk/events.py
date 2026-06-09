"""Event types and payloads for nahida-bot plugin communication."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar
from uuid import UUID, uuid4

from nahida_bot_sdk.chat_address import ChatAddress

PayloadT = TypeVar("PayloadT")
EventT = TypeVar("EventT", bound="Event[Any]", contravariant=True)


@dataclass(slots=True, frozen=True)
class Event(Generic[PayloadT]):
    """Base typed event model."""

    payload: PayloadT
    event_id: UUID = field(default_factory=uuid4)
    trace_id: str = ""
    source: str = ""
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))


# ── Application Lifecycle Events ──────────────────────────────


@dataclass(slots=True, frozen=True)
class AppLifecyclePayload:
    """Payload used by application lifecycle events."""

    app_name: str
    debug: bool


@dataclass(slots=True, frozen=True)
class AppInitializing(Event[AppLifecyclePayload]):
    """Raised before application initialization starts."""


@dataclass(slots=True, frozen=True)
class AppStarted(Event[AppLifecyclePayload]):
    """Raised after application startup completes."""


@dataclass(slots=True, frozen=True)
class AppStopping(Event[AppLifecyclePayload]):
    """Raised before application shutdown starts."""


@dataclass(slots=True, frozen=True)
class AppStopped(Event[AppLifecyclePayload]):
    """Raised after application shutdown completes."""


# ── Plugin Events ─────────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class PluginPayload:
    """Payload for plugin lifecycle events."""

    plugin_id: str
    plugin_name: str
    plugin_version: str


@dataclass(slots=True, frozen=True)
class PluginLoaded(Event[PluginPayload]):
    """Raised after a plugin has been loaded."""


@dataclass(slots=True, frozen=True)
class PluginEnabled(Event[PluginPayload]):
    """Raised after a plugin has been enabled."""


@dataclass(slots=True, frozen=True)
class PluginDisabled(Event[PluginPayload]):
    """Raised after a plugin has been disabled."""


@dataclass(slots=True, frozen=True)
class PluginUnloaded(Event[PluginPayload]):
    """Raised after a plugin has been fully unloaded."""


@dataclass(slots=True, frozen=True)
class PluginErrorPayload:
    """Payload for plugin error events."""

    plugin_id: str
    plugin_name: str
    method: str
    error: str


@dataclass(slots=True, frozen=True)
class PluginErrorOccurred(Event[PluginErrorPayload]):
    """Raised when a plugin method raises an unhandled exception."""


# ── Message Events ────────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class MessagePayload:
    """Payload for message lifecycle events."""

    message: Any  # InboundMessage — use Any to avoid circular import
    session_id: str


@dataclass(slots=True, frozen=True)
class MessageReceived(Event[MessagePayload]):
    """Raised after a channel service plugin normalizes an inbound event."""


@dataclass(slots=True, frozen=True)
class MessageObserved(Event[MessagePayload]):
    """Raised for inbound messages recorded as context but not handled by agent."""


@dataclass(slots=True, frozen=True)
class AgentResponseRequestPayload:
    """Payload for a plugin-initiated request to run the main agent."""

    message: Any  # InboundMessage — use Any to avoid circular import
    session_id: str
    chat_address: ChatAddress
    requester_plugin_id: str
    reason: str
    instruction: str = ""
    synthetic: bool = False
    observed_messages: tuple[Any, ...] = ()
    reply_to_message_id: str | None = None


@dataclass(slots=True, frozen=True)
class AgentResponseRequested(Event[AgentResponseRequestPayload]):
    """Raised when a plugin asks the router to let the agent join a chat."""


@dataclass(slots=True, frozen=True)
class MessageSending(Event[MessagePayload]):
    """Raised before sending a message for observation and audit hooks."""


@dataclass(slots=True, frozen=True)
class MessageSent(Event[MessagePayload]):
    """Raised after a message has been successfully sent."""
