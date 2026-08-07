"""Event types and payloads for nahida-bot plugin communication."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Generic, Literal, TypeVar
from uuid import UUID, uuid4

from nahida_bot_sdk.chat_address import ChatAddress
from nahida_bot_sdk.messaging import AttentionFrame

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
    # OutboundMessage for MessageSending/MessageSent. Kept separate from
    # ``message`` so existing subscribers that use the originating inbound
    # message for chat correlation remain backwards-compatible.
    outbound: Any | None = None
    # Explicit turn boundaries. Empty values preserve the legacy channel
    # contract while node/WebUI transports can avoid overloading session_id.
    conversation_id: str = ""
    transport_address: str = ""
    reply_route: str = ""
    credential_id: str = ""
    actor_account_key: str = ""


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
    attention_frame: AttentionFrame | None = None


@dataclass(slots=True, frozen=True)
class AgentResponseRequested(Event[AgentResponseRequestPayload]):
    """Raised when a plugin asks the router to let the agent join a chat."""


@dataclass(slots=True, frozen=True)
class MessageSending(Event[MessagePayload]):
    """Raised before sending a message for observation and audit hooks."""


@dataclass(slots=True, frozen=True)
class MessageSent(Event[MessagePayload]):
    """Raised after a message has been successfully sent."""


# ── Interaction Events ────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class PokePayload:
    """Payload for inbound poke (戳一戳) events.

    Emitted for Milky ``friend_nudge`` / ``group_nudge`` events that target the
    bot. No agent response is wired by default — subscribers opt in.
    """

    session_id: str
    chat_address: ChatAddress
    scene: Literal["friend", "group"]
    group_id: str  # "" for the friend scene
    user_id: str  # the poker (sender)
    target_user_id: str  # the receiver; for friend self-receive this is the bot
    display_action: str
    display_suffix: str
    raw: dict[str, Any]  # preserves display_action_img_url and other fields


@dataclass(slots=True, frozen=True)
class PokeEvent(Event[PokePayload]):
    """Raised when the bot is poked (group) or receives a friend nudge."""


@dataclass(slots=True, frozen=True)
class MessageReactionPayload:
    """Payload for inbound group emoji-reply (表情回复) events.

    Emitted for Milky ``group_message_reaction`` events. Note: there is no
    cheap filter for "reactions on the bot's own messages" — that would require
    tracking every sent message_seq — so all reactions in allowed groups are
    captured and a future subscriber correlates via ``message_seq``.
    """

    session_id: str
    chat_address: ChatAddress
    group_id: str
    user_id: str  # the reactor
    message_seq: str
    face_id: str
    reaction_type: str  # "face" | "emoji" (Milky >=1.2); "" when absent
    is_add: bool
    raw: dict[str, Any]


@dataclass(slots=True, frozen=True)
class MessageReactionEvent(Event[MessageReactionPayload]):
    """Raised when a group message receives an emoji reaction."""


# ── Agent Run Events ──────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class AgentStopPayload:
    """Payload for a request to stop an in-flight agent run."""

    session_id: str


@dataclass(slots=True, frozen=True)
class AgentStopRequested(Event[AgentStopPayload]):
    """Raised to request graceful cancellation of the active run for a session.

    Decouples stop sources (``/stop``, ``/new``, future webui/watchdog) from the
    router's run tracker: any component may publish this; the router subscribes
    and maps it to ``request_stop``.
    """


@dataclass(slots=True, frozen=True)
class AgentRunPayload:
    """Payload for agent-run lifecycle events."""

    session_id: str
    workspace_id: str = ""
    # ``terminal`` is set on the terminal events only ("cancelled" / "completed" /
    # "failed" / "incomplete" / "crashed"); empty on ``AgentRunStarted``.
    terminal: str = ""
    error: str = ""


@dataclass(slots=True, frozen=True)
class AgentRunStarted(Event[AgentRunPayload]):
    """Raised when an agent run begins executing for a session."""


@dataclass(slots=True, frozen=True)
class AgentRunCancelled(Event[AgentRunPayload]):
    """Raised when an agent run ends because it was stopped/cancelled."""


@dataclass(slots=True, frozen=True)
class AgentRunFinished(Event[AgentRunPayload]):
    """Raised when an agent run ends any way other than cancellation.

    Covers natural completion, max_steps, provider error, and crashes. Exactly
    one of ``AgentRunCancelled`` / ``AgentRunFinished`` is emitted per run.
    """


# ── Process Supervisor Events ─────────────────────────────────


@dataclass(slots=True, frozen=True)
class ProcessPayload:
    """Payload for supervised-process lifecycle events.

    Emitted by the core :class:`ProcessSupervisor` when a managed OS subprocess
    changes state. The ``status`` field mirrors :class:`ProcessInfo.status`.
    Plugins and the WebUI subscribe to these for unified observability of
    sidecars (SSH tunnels, frpc, cloudflared, etc.).
    """

    name: str
    owner: str
    status: str
    pid: int | None
    restart_count: int
    exit_code: int | None
    error: str = ""


@dataclass(slots=True, frozen=True)
class ProcessStarted(Event[ProcessPayload]):
    """Raised when a supervised process enters the running state."""


@dataclass(slots=True, frozen=True)
class ProcessStopped(Event[ProcessPayload]):
    """Raised when a supervised process stops (clean exit or manual stop)."""


@dataclass(slots=True, frozen=True)
class ProcessFailed(Event[ProcessPayload]):
    """Raised when a supervised process fails or trips the restart circuit breaker."""
