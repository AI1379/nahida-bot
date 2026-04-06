"""Typed in-process event bus for the core runtime."""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
    from nahida_bot.core.config import Settings
    from nahida_bot.core.app import Application

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


@dataclass(slots=True)
class EventContext:
    """Dependencies injected into each event handler call."""

    app: Application
    settings: Settings
    logger: logging.Logger


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


class EventBus:
    """Lightweight typed event bus with handler error isolation."""

    def __init__(self, context: EventContext) -> None:
        self._context = context
        self._handlers: dict[
            type[Event[Any]],
            list[Callable[[Event[Any], EventContext], Awaitable[None] | None]],
        ] = {}
        self._closed = False
        self._pending_tasks: set[asyncio.Task[None]] = set()

    def subscribe(
        self,
        event_type: type[EventT],
        handler: EventHandler[EventT],
    ) -> Subscription:
        """Subscribe a handler to one concrete event type."""
        if self._closed:
            raise EventBusClosedError("EventBus is already closed")

        normalized_handler = cast(
            Callable[[Event[Any], EventContext], Awaitable[None] | None],
            handler,
        )
        handlers = self._handlers.setdefault(cast(type[Event[Any]], event_type), [])
        handlers.append(normalized_handler)

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
        handlers = self._handlers.get(event_type)
        if not handlers:
            return

        self._handlers[event_type] = [h for h in handlers if h is not handler]
        if not self._handlers[event_type]:
            self._handlers.pop(event_type, None)

    async def publish(self, event: Event[Any]) -> PublishResult:
        """Publish one event and wait for all handlers to complete."""
        if self._closed:
            raise EventBusClosedError("EventBus is already closed")

        handlers = list(self._handlers.get(type(event), []))
        failures: list[HandlerFailure] = []

        for handler in handlers:
            try:
                outcome = handler(event, self._context)
                if inspect.isawaitable(outcome):
                    await outcome
            except Exception as exc:  # noqa: BLE001
                failures.append(
                    HandlerFailure(
                        handler_name=getattr(
                            handler, "__name__", handler.__class__.__name__
                        ),
                        error=str(exc),
                    )
                )
                self._context.logger.exception("Event handler failed", exc_info=exc)

        return PublishResult(dispatched=len(handlers), failures=tuple(failures))

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
