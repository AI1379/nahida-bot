"""Tests for typed event bus."""

from dataclasses import dataclass

import pytest

from nahida_bot.core.app import Application
from nahida_bot.core.config import Settings
from nahida_bot.core.events import (
    AppInitializing,
    AppStarted,
    AppStopped,
    AppStopping,
    Event,
    EventContext,
)


@dataclass(slots=True)
class SamplePayload:
    """Simple payload model for tests."""

    value: str


class SampleEvent(Event[SamplePayload]):
    """Concrete test event."""


@pytest.mark.asyncio
async def test_event_bus_subscribe_and_publish() -> None:
    """Event bus should dispatch a typed event to subscribers."""
    app = Application(settings=Settings(app_name="Events Test", debug=True))

    received: list[str] = []

    async def handler(event: SampleEvent, ctx: EventContext) -> None:
        received.append(event.payload.value)
        assert ctx.settings.app_name == "Events Test"

    app.event_bus.subscribe(SampleEvent, handler)
    result = await app.event_bus.publish(SampleEvent(payload=SamplePayload("ok")))

    assert result.dispatched == 1
    assert result.failures == ()
    assert received == ["ok"]


@pytest.mark.asyncio
async def test_event_bus_handler_failure_is_isolated() -> None:
    """One failing handler should not stop other handlers."""
    app = Application(settings=Settings())
    called = {"good": False}

    async def bad_handler(event: SampleEvent, ctx: EventContext) -> None:  # noqa: ARG001
        raise RuntimeError("boom")

    async def good_handler(event: SampleEvent, ctx: EventContext) -> None:  # noqa: ARG001
        called["good"] = True

    app.event_bus.subscribe(SampleEvent, bad_handler)
    app.event_bus.subscribe(SampleEvent, good_handler)

    result = await app.event_bus.publish(SampleEvent(payload=SamplePayload("x")))

    assert result.dispatched == 2
    assert len(result.failures) == 1
    assert "boom" in result.failures[0].error
    assert called["good"] is True


@pytest.mark.asyncio
async def test_application_lifecycle_events_are_emitted() -> None:
    """Application should publish lifecycle events through EventBus."""
    app = Application(settings=Settings(app_name="Lifecycle", debug=False))
    sequence: list[str] = []

    async def on_init(event: AppInitializing, ctx: EventContext) -> None:  # noqa: ARG001
        sequence.append(type(event).__name__)

    async def on_started(event: AppStarted, ctx: EventContext) -> None:  # noqa: ARG001
        sequence.append(type(event).__name__)

    async def on_stopping(event: AppStopping, ctx: EventContext) -> None:  # noqa: ARG001
        sequence.append(type(event).__name__)

    async def on_stopped(event: AppStopped, ctx: EventContext) -> None:  # noqa: ARG001
        sequence.append(type(event).__name__)

    app.event_bus.subscribe(AppInitializing, on_init)
    app.event_bus.subscribe(AppStarted, on_started)
    app.event_bus.subscribe(AppStopping, on_stopping)
    app.event_bus.subscribe(AppStopped, on_stopped)

    await app.initialize()
    await app.start()
    await app.stop()

    assert sequence == ["AppInitializing", "AppStarted", "AppStopping", "AppStopped"]
