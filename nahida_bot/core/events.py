"""Typed in-process event bus for the core runtime."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import IntEnum
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Generic,
    Protocol,
    TypeVar,
    cast,
)
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from nahida_bot.core.app import Application
    from nahida_bot.core.config import Settings

PayloadT = TypeVar("PayloadT")
EventT = TypeVar("EventT", bound="Event[Any]", contravariant=True)


class LoggerLike(Protocol):
    """Minimal logger protocol consumed by EventBus."""

    def exception(self, event: str, **kwargs: object) -> object:
        """Log one exception event."""

    def warning(self, event: str, **kwargs: object) -> object:
        """Log one warning event."""


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
    """Raised after a plugin has been loaded (module imported, class instantiated)."""


@dataclass(slots=True, frozen=True)
class PluginEnabled(Event[PluginPayload]):
    """Raised after a plugin has been enabled (on_load + on_enable called)."""


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
class MessageSending(Event[MessagePayload]):
    """Raised before sending a message for observation and audit hooks."""


@dataclass(slots=True, frozen=True)
class MessageSent(Event[MessagePayload]):
    """Raised after a message has been successfully sent."""


# ── Event Bus Infrastructure ──────────────────────────────────


@dataclass(slots=True)
class EventContext:
    """Dependencies injected into each event handler call."""

    app: Application
    settings: Settings
    logger: LoggerLike


class EventHandler(Protocol[EventT]):
    """Typed event handler protocol."""

    async def __call__(self, event: EventT, ctx: EventContext) -> None:
        """Handle one event."""


class EventBusError(Exception):
    """Base event bus error."""


class EventBusClosedError(EventBusError):
    """Raised when publishing/subscribing after shutdown."""


@dataclass(slots=True, frozen=True)
class HandlerFailure:
    """One handler failure entry in a publish result."""

    handler_name: str
    error: str


@dataclass(slots=True, frozen=True)
class PublishResult:
    """Result of a publish operation."""

    dispatched: int
    failures: tuple[HandlerFailure, ...]


@dataclass(slots=True)
class Subscription:
    """Subscription handle used for explicit unsubscription."""

    event_type: type[Event[Any]]
    handler: Callable[[Event[Any], EventContext], Awaitable[None] | None]
    bus: "EventBus"

    def unsubscribe(self) -> None:
        """Detach this handler from the bus."""
        self.bus.unsubscribe(self.event_type, self.handler)


@dataclass(slots=True)
class _HandlerEntry:
    """Internal bookkeeping for a registered handler."""

    handler: Callable[[Event[Any], EventContext], Awaitable[None] | None]
    priority: int
    timeout: float  # seconds, used for async-phase handlers
    name: str


class _HandlerPhase(IntEnum):
    """Which execution phase a handler belongs to."""

    SYNC = 0  # priority <= 0, executed serially
    ASYNC = 1  # priority > 0, executed concurrently with timeout


class EventBus:
    """Lightweight typed event bus with priority-based two-phase dispatch.

    Handlers with ``priority <= 0`` execute serially in priority order
    (core handlers). Handlers with ``priority > 0`` execute concurrently
    with per-handler timeout protection (plugin handlers).
    """

    def __init__(self, context: EventContext) -> None:
        self._context = context
        self._handlers: dict[type[Event[Any]], list[_HandlerEntry]] = {}
        self._closed = False
        self._pending_tasks: set[asyncio.Task[None]] = set()

    @property
    def context(self) -> EventContext:
        """Return the dependency context shared with event handlers."""
        return self._context

    def subscribe(
        self,
        event_type: type[EventT],
        handler: EventHandler[EventT],
        *,
        priority: int = 0,
        timeout: float = 30.0,
    ) -> Subscription:
        """Subscribe a handler to one concrete event type.

        Args:
            event_type: The event class to listen for.
            handler: Async or sync callback accepting (event, ctx).
            priority: Execution order — lower runs first. Values <= 0 run
                serially (core); values > 0 run concurrently (plugins).
            timeout: Per-handler timeout in seconds for async-phase handlers.

        Returns:
            A ``Subscription`` that can be used to unsubscribe.
        """
        if self._closed:
            raise EventBusClosedError("EventBus is already closed")

        normalized_handler = cast(
            Callable[[Event[Any], EventContext], Awaitable[None] | None],
            handler,
        )
        entry = _HandlerEntry(
            handler=normalized_handler,
            priority=priority,
            timeout=timeout,
            name=getattr(handler, "__name__", handler.__class__.__name__),
        )
        entries = self._handlers.setdefault(cast(type[Event[Any]], event_type), [])
        entries.append(entry)

        return Subscription(
            event_type=cast(type[Event[Any]], event_type),
            handler=normalized_handler,
            bus=self,
        )

    def unsubscribe(
        self,
        event_type: type[Event[Any]],
        handler: Callable[[Event[Any], EventContext], Awaitable[None] | None],
    ) -> None:
        """Unsubscribe a handler from one event type."""
        entries = self._handlers.get(event_type)
        if not entries:
            return

        self._handlers[event_type] = [e for e in entries if e.handler is not handler]
        if not self._handlers[event_type]:
            self._handlers.pop(event_type, None)

    async def publish(self, event: Event[Any]) -> PublishResult:
        """Publish one event with two-phase handler dispatch.

        Phase 1 (sync, priority <= 0): handlers execute serially in
        ascending priority order. A failing handler does not prevent
        subsequent handlers from running.

        Phase 2 (async, priority > 0): handlers execute concurrently with
        per-handler timeout protection. A timeout or error in one handler
        does not affect others.
        """
        if self._closed:
            raise EventBusClosedError("EventBus is already closed")

        entries = list(self._handlers.get(type(event), []))
        if not entries:
            return PublishResult(dispatched=0, failures=())

        # Sort by priority (ascending — lower priority runs first)
        entries.sort(key=lambda e: e.priority)

        sync_entries = [e for e in entries if e.priority <= 0]
        async_entries = [e for e in entries if e.priority > 0]

        failures: list[HandlerFailure] = []

        # Phase 1: serial execution for core handlers
        for entry in sync_entries:
            try:
                outcome = entry.handler(event, self._context)
                if inspect.isawaitable(outcome):
                    await outcome
            except Exception as exc:  # noqa: BLE001
                failures.append(HandlerFailure(handler_name=entry.name, error=str(exc)))
                self._context.logger.exception("Event handler failed", exc_info=exc)

        # Phase 2: concurrent execution with per-handler timeout
        if async_entries:

            async def _run_with_timeout(entry: _HandlerEntry) -> None:
                try:
                    outcome = entry.handler(event, self._context)
                    if inspect.isawaitable(outcome):
                        await asyncio.wait_for(outcome, timeout=entry.timeout)
                except TimeoutError:
                    failures.append(
                        HandlerFailure(
                            handler_name=entry.name,
                            error=f"Handler timed out after {entry.timeout}s",
                        )
                    )
                    self._context.logger.warning(
                        "Event handler timed out",
                        handler=entry.name,
                        timeout=entry.timeout,
                    )
                except Exception as exc:  # noqa: BLE001
                    failures.append(
                        HandlerFailure(handler_name=entry.name, error=str(exc))
                    )
                    self._context.logger.exception("Event handler failed", exc_info=exc)

            await asyncio.gather(
                *[_run_with_timeout(e) for e in async_entries],
                return_exceptions=False,
            )

        return PublishResult(
            dispatched=len(sync_entries) + len(async_entries),
            failures=tuple(failures),
        )

    def publish_nowait(self, event: Event[Any]) -> bool:
        """Publish one event in background.

        Returns False if the bus is closed and event cannot be scheduled.
        """
        if self._closed:
            return False

        task = asyncio.create_task(self._publish_nowait_task(event))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)
        return True

    async def _publish_nowait_task(self, event: Event[Any]) -> None:
        """Internal wrapper to keep no-wait tasks isolated."""
        try:
            await self.publish(event)
        except EventBusClosedError:
            return

    async def shutdown(self, timeout: float | None = None) -> None:
        """Close the bus and wait for in-flight no-wait tasks."""
        self._closed = True
        if not self._pending_tasks:
            return

        pending = tuple(self._pending_tasks)
        if timeout is None:
            await asyncio.gather(*pending, return_exceptions=True)
            return

        try:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True),
                timeout=timeout,
            )
        except TimeoutError:
            self._context.logger.warning(
                "EventBus shutdown timed out with pending tasks"
            )
