"""Tests for core-event to Gateway-Node event translation."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from nahida_bot.core.events import (
    AgentRunFinished,
    AgentRunPayload,
    AgentRunStarted,
    EventBus,
    EventContext,
    MessagePayload,
    MessageReceived,
    MessageSent,
)
from nahida_bot.gateway.node_protocol.schemas import NodeEnvelope
from nahida_bot.gateway.node_protocol.sessions import NodeSession
from nahida_bot.gateway.services.node_event_bridge import NodeEventBridge
from nahida_bot.gateway.services.node_registry import NodeRegistry
from nahida_bot.plugins.base import OutboundMessage


@pytest.mark.asyncio
async def test_bridge_emits_one_started_and_one_completed_with_text() -> None:
    app = SimpleNamespace()
    app.event_bus = EventBus(
        EventContext(app=cast(Any, app), settings=None, logger=MagicMock())
    )
    registry = NodeRegistry()
    session = NodeSession(session_id="temp", node_id="desktop-1")
    sent: list[NodeEnvelope] = []

    async def send(envelope: NodeEnvelope) -> None:
        sent.append(envelope)

    session.send = send
    registry.register_session(
        session,
        node_id="desktop-1",
        display_name="Desktop",
        node_type="desktop",
        capabilities=[],
        metadata={},
    )
    bridge = NodeEventBridge(cast(Any, app), registry)
    await bridge.start()
    try:
        await app.event_bus.publish(
            MessageReceived(
                payload=MessagePayload(message=object(), session_id="test:private:c1"),
                source="test",
            )
        )
        await app.event_bus.publish(
            AgentRunStarted(
                payload=AgentRunPayload(session_id="test:private:c1"),
                source="message_router",
            )
        )
        await app.event_bus.publish(
            MessageSent(
                payload=MessagePayload(
                    message=object(),
                    session_id="test:private:c1",
                    outbound=OutboundMessage(
                        text="真实回复",
                        extra={"display_plan": {"version": "1.0", "text": "真实回复"}},
                    ),
                ),
                source="message_router",
            )
        )
        await app.event_bus.publish(
            AgentRunFinished(
                payload=AgentRunPayload(
                    session_id="test:private:c1",
                    terminal="completed",
                ),
                source="message_router",
            )
        )
    finally:
        await bridge.stop()

    assert [envelope.event for envelope in sent] == [
        "agent.message.started",
        "agent.message.completed",
    ]
    assert sent[1].payload == {
        "session_id": "test:private:c1",
        "text": "真实回复",
        "display_plan": {"version": "1.0", "text": "真实回复"},
    }


@pytest.mark.asyncio
async def test_bridge_finishes_run_without_outbound_message() -> None:
    app = SimpleNamespace()
    app.event_bus = EventBus(
        EventContext(app=cast(Any, app), settings=None, logger=MagicMock())
    )
    registry = NodeRegistry()
    session = NodeSession(session_id="temp", node_id="desktop-1")
    sent: list[NodeEnvelope] = []

    async def send(envelope: NodeEnvelope) -> None:
        sent.append(envelope)

    session.send = send
    registry.register_session(
        session,
        node_id="desktop-1",
        display_name="Desktop",
        node_type="desktop",
        capabilities=[],
        metadata={},
    )
    bridge = NodeEventBridge(cast(Any, app), registry)
    await bridge.start()
    try:
        await app.event_bus.publish(
            AgentRunStarted(
                payload=AgentRunPayload(session_id="test:private:c1"),
                source="message_router",
            )
        )
        await app.event_bus.publish(
            AgentRunFinished(
                payload=AgentRunPayload(
                    session_id="test:private:c1",
                    terminal="failed",
                    error="provider failed",
                ),
                source="message_router",
            )
        )
    finally:
        await bridge.stop()

    assert [envelope.event for envelope in sent] == [
        "agent.message.started",
        "agent.message.completed",
    ]
    assert sent[1].payload == {
        "session_id": "test:private:c1",
        "text": "",
        "terminal": "failed",
        "error": "provider failed",
    }


@pytest.mark.asyncio
async def test_bridge_routes_node_reply_only_to_originating_node() -> None:
    app = SimpleNamespace()
    app.event_bus = EventBus(
        EventContext(app=cast(Any, app), settings=None, logger=MagicMock())
    )
    registry = NodeRegistry()
    deliveries: dict[str, list[NodeEnvelope]] = {
        "desktop-1": [],
        "desktop-2": [],
    }

    for node_id in deliveries:
        session = NodeSession(session_id=f"session-{node_id}", node_id=node_id)

        async def send(
            envelope: NodeEnvelope,
            *,
            target: str = node_id,
        ) -> None:
            deliveries[target].append(envelope)

        session.send = send
        registry.register_session(
            session,
            node_id=node_id,
            display_name=node_id,
            node_type="desktop",
            capabilities=[],
            metadata={},
        )

    bridge = NodeEventBridge(cast(Any, app), registry)
    await bridge.start()
    try:
        await app.event_bus.publish(
            MessageSent(
                payload=MessagePayload(
                    message=object(),
                    session_id="conversation:private:owner-desktop",
                    conversation_id="conversation:private:owner-desktop",
                    reply_route="node:desktop-1",
                    outbound=OutboundMessage(text="only desktop 1"),
                ),
                source="message_router",
            )
        )
    finally:
        await bridge.stop()

    assert [item.event for item in deliveries["desktop-1"]] == [
        "agent.message.completed"
    ]
    assert deliveries["desktop-2"] == []
