"""Tests for core-event to Gateway-Node event routing."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from nahida_bot.core.events import (
    AgentRunFinished,
    AgentRunPayload,
    AgentRunStarted,
    AppLifecyclePayload,
    AppStopping,
    EventBus,
    EventContext,
    MessagePayload,
    MessageReceived,
    MessageSent,
    PluginErrorOccurred,
    PluginErrorPayload,
    SchedulerNotification,
    SchedulerNotificationPayload,
)
from nahida_bot.gateway.node_protocol.schemas import NodeEnvelope
from nahida_bot.gateway.node_protocol.sessions import NodeSession
from nahida_bot.gateway.services.node_event_bridge import NodeEventBridge
from nahida_bot.gateway.services.node_registry import NodeRegistry
from nahida_bot.plugins.base import OutboundMessage


@pytest.mark.asyncio
async def test_bridge_routes_node_reply_to_target_node_only() -> None:
    """MessageSent with reply_route="node:desktop-1" is delivered only to
    desktop-1, not to desktop-2."""
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
                    session_id="conversation:private:owner",
                    conversation_id="conversation:private:owner",
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


@pytest.mark.asyncio
async def test_bridge_ignores_channel_routed_events() -> None:
    """MessageSent with a Milky (non-node) reply_route is not forwarded to
    any connected node."""
    app = SimpleNamespace()
    app.event_bus = EventBus(
        EventContext(app=cast(Any, app), settings=None, logger=MagicMock())
    )
    registry = NodeRegistry()
    session = NodeSession(session_id="session-desktop-1", node_id="desktop-1")
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
            MessageSent(
                payload=MessagePayload(
                    message=object(),
                    session_id="milky:group:123",
                    reply_route="milky:group:123",
                    outbound=OutboundMessage(text="QQ reply"),
                ),
                source="message_router",
            )
        )
    finally:
        await bridge.stop()

    assert sent == []


@pytest.mark.asyncio
async def test_bridge_routes_agent_lifecycle_via_captured_node_reply_route() -> None:
    app = SimpleNamespace()
    app.event_bus = EventBus(
        EventContext(app=cast(Any, app), settings=None, logger=MagicMock())
    )
    registry = NodeRegistry()
    deliveries: dict[str, list[NodeEnvelope]] = {"desktop-1": [], "desktop-2": []}
    for node_id in deliveries:
        session = NodeSession(session_id="initial", node_id=node_id)

        async def send(envelope: NodeEnvelope, *, target: str = node_id) -> None:
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
            MessageReceived(
                payload=MessagePayload(
                    message=object(),
                    session_id="test:private:c1",
                    reply_route="node:desktop-1",
                ),
                source="node:desktop-1",
            )
        )
        await app.event_bus.publish(
            AgentRunStarted(payload=AgentRunPayload(session_id="test:private:c1"))
        )
        await app.event_bus.publish(
            AgentRunFinished(
                payload=AgentRunPayload(
                    session_id="test:private:c1",
                    terminal="failed",
                    error="provider unavailable",
                )
            )
        )
    finally:
        await bridge.stop()

    assert [item.event for item in deliveries["desktop-1"]] == [
        "agent.message.started",
        "agent.message.error",
    ]
    assert deliveries["desktop-1"][1].payload == {
        "session_id": "test:private:c1",
        "error": "provider unavailable",
        "terminal": "failed",
    }
    assert deliveries["desktop-2"] == []


@pytest.mark.asyncio
async def test_bridge_uses_unique_explicit_node_conversation_binding() -> None:
    app = SimpleNamespace()
    app.event_bus = EventBus(
        EventContext(app=cast(Any, app), settings=None, logger=MagicMock())
    )
    registry = NodeRegistry()
    sent: list[NodeEnvelope] = []
    session = NodeSession(
        session_id="initial",
        node_id="desktop-1",
        conversation_id="test:private:c1",
    )

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
            AgentRunStarted(payload=AgentRunPayload(session_id="test:private:c1"))
        )
        await app.event_bus.publish(
            AgentRunFinished(
                payload=AgentRunPayload(
                    session_id="test:private:c1", terminal="completed"
                )
            )
        )
        second_session = NodeSession(
            session_id="initial-2",
            node_id="desktop-2",
            conversation_id="test:private:c1",
        )
        second_session.send = send
        registry.register_session(
            second_session,
            node_id="desktop-2",
            display_name="Desktop 2",
            node_type="desktop",
            capabilities=[],
            metadata={},
        )
        await app.event_bus.publish(
            AgentRunStarted(payload=AgentRunPayload(session_id="test:private:c1"))
        )
    finally:
        await bridge.stop()

    # A unique credential conversation binding is routable; once ambiguous,
    # fail closed rather than leaking the session event to either node.
    assert [item.event for item in sent] == ["agent.message.started"]


@pytest.mark.asyncio
async def test_bridge_sends_only_global_protocol_notifications_to_all_nodes() -> None:
    app = SimpleNamespace()
    app.event_bus = EventBus(
        EventContext(app=cast(Any, app), settings=None, logger=MagicMock())
    )
    registry = NodeRegistry()
    sent: list[NodeEnvelope] = []
    session = NodeSession(session_id="initial", node_id="desktop-1")

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
            PluginErrorOccurred(
                payload=PluginErrorPayload(
                    plugin_id="weather",
                    plugin_name="Weather",
                    method="on_message",
                    error="boom",
                )
            )
        )
        await app.event_bus.publish(
            AppStopping(payload=AppLifecyclePayload(app_name="nahida", debug=False))
        )
    finally:
        await bridge.stop()

    assert [item.event for item in sent] == ["plugin.error", "gateway.shutdown"]
    assert sent[0].payload == {
        "plugin_id": "weather",
        "plugin_name": "Weather",
        "method": "on_message",
        "error": "boom",
    }
    assert sent[1].payload == {
        "reason": "application_stopping",
        "retry_after_ms": 5000,
    }


@pytest.mark.asyncio
async def test_bridge_ignores_events_without_reply_route() -> None:
    """AgentRunStarted and MessageSent without reply_route are not forwarded
    to any node — nodes only receive explicitly routed events."""
    app = SimpleNamespace()
    app.event_bus = EventBus(
        EventContext(app=cast(Any, app), settings=None, logger=MagicMock())
    )
    registry = NodeRegistry()
    session = NodeSession(session_id="session-desktop-1", node_id="desktop-1")
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
            MessageSent(
                payload=MessagePayload(
                    message=object(),
                    session_id="test:private:c1",
                    outbound=OutboundMessage(text="no route"),
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

    assert sent == []


@pytest.mark.asyncio
async def test_bridge_routes_scheduler_notification_by_unique_conversation() -> None:
    app = SimpleNamespace()
    app.event_bus = EventBus(
        EventContext(app=cast(Any, app), settings=None, logger=MagicMock())
    )
    registry = NodeRegistry()
    deliveries: dict[str, list[NodeEnvelope]] = {"desktop-1": [], "desktop-2": []}
    for node_id, conversation_id in (
        ("desktop-1", "test:private:c1"),
        ("desktop-2", "test:private:c2"),
    ):
        session = NodeSession(
            session_id=f"session-{node_id}",
            node_id=node_id,
            conversation_id=conversation_id,
        )

        async def send(envelope: NodeEnvelope, *, target: str = node_id) -> None:
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
            SchedulerNotification(
                payload=SchedulerNotificationPayload(
                    job_id="cron-1",
                    session_id="test:private:c1:cron:cron-1",
                    conversation_id="test:private:c1",
                    text="Time to rest.",
                )
            )
        )
    finally:
        await bridge.stop()

    assert [item.event for item in deliveries["desktop-1"]] == ["notification.reminder"]
    assert deliveries["desktop-1"][0].payload == {
        "job_id": "cron-1",
        "session_id": "test:private:c1:cron:cron-1",
        "conversation_id": "test:private:c1",
        "message": "Time to rest.",
    }
    assert deliveries["desktop-2"] == []
