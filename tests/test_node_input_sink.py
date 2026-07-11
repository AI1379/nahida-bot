"""Tests for the application-backed node input bridge."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from nahida_bot.core.events import EventBus, EventContext, MessageReceived
from nahida_bot.gateway.node_protocol.errors import NodeInputUnavailable
from nahida_bot.gateway.services.node_input_sink import ApplicationNodeInputSink


class _Channels:
    def __init__(self, connected: bool = True) -> None:
        self.connected = connected

    def get(self, channel: str) -> object | None:
        return object() if self.connected and channel == "test" else None


def _app(*, connected: bool = True) -> Any:
    app = SimpleNamespace(
        message_router=object(), channel_registry=_Channels(connected)
    )
    app.event_bus = EventBus(
        EventContext(app=cast(Any, app), settings=None, logger=MagicMock())
    )
    return app


@pytest.mark.asyncio
async def test_submit_publishes_typed_node_message() -> None:
    app = _app()
    seen: list[MessageReceived] = []

    async def capture(event: MessageReceived, ctx: EventContext) -> None:
        seen.append(event)

    app.event_bus.subscribe(MessageReceived, capture)
    sink = ApplicationNodeInputSink(app)

    await sink.submit(
        node_id="desktop-1",
        credential_id="nt_desktop_1",
        actor_account_key="desktop:user:owner",
        conversation_id="conversation:private:owner-desktop",
        text="  hello from desktop  ",
    )

    assert len(seen) == 1
    event = seen[0]
    assert event.source == "node:desktop-1"
    assert event.payload.session_id == "conversation:private:owner-desktop"
    assert event.payload.reply_route == "node:desktop-1"
    assert event.payload.actor_account_key == "desktop:user:owner"
    assert event.payload.message.text == "hello from desktop"
    assert event.payload.message.user_id == "desktop:user:owner"
    assert event.payload.message.raw_event["source"] == "node"
    assert event.payload.message.message_context is not None
    assert "source:node" in event.payload.message.message_context.extra_tags


@pytest.mark.asyncio
async def test_submit_rejects_untyped_or_unbound_targets() -> None:
    with pytest.raises(ValueError, match="typed address"):
        await ApplicationNodeInputSink(_app()).submit(
            node_id="desktop-1",
            credential_id="nt_desktop_1",
            actor_account_key="desktop:user:owner",
            conversation_id="test:c1",
            text="hello",
        )

    with pytest.raises(NodeInputUnavailable, match="not bound"):
        await ApplicationNodeInputSink(_app(connected=False)).submit(
            node_id="desktop-1",
            credential_id="nt_desktop_1",
            actor_account_key="",
            conversation_id="conversation:private:owner-desktop",
            text="hello",
        )


@pytest.mark.asyncio
async def test_submit_does_not_require_a_channel_for_node_conversation() -> None:
    app = _app(connected=False)
    seen: list[MessageReceived] = []

    async def capture(event: MessageReceived, ctx: EventContext) -> None:
        seen.append(event)

    app.event_bus.subscribe(MessageReceived, capture)
    await ApplicationNodeInputSink(app).submit(
        node_id="desktop-1",
        credential_id="nt_desktop_1",
        actor_account_key="desktop:user:owner",
        conversation_id="conversation:private:owner-desktop",
        text="hello",
    )

    assert len(seen) == 1
