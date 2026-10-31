"""Authenticated state transport and service calls must not trust client identity."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from nahida_bot.gateway.node_protocol.dispatcher import NodeDispatcher
from nahida_bot.gateway.node_protocol.plugin_handlers import register_plugin_handlers
from nahida_bot.gateway.node_protocol.schemas import (
    NodeCapability,
    build_request as protocol_request,
)
from nahida_bot.gateway.node_protocol.sessions import NodeSession, NodeSessionState
from nahida_bot.gateway.services.desktop_delivery import DesktopDeliveryService
from nahida_bot.gateway.services.node_registry import NodeRegistry
from nahida_bot.plugins.services import PluginServiceRegistry, ServiceEntry
from nahida_bot_sdk.services import PluginServiceContext


def build_request(method, request_id, payload):
    return protocol_request(method, request_id=request_id, payload=payload)


async def test_node_context_is_sequenced_and_scoped_to_connection():
    session = NodeSession("session", "pc", state=NodeSessionState.ONLINE)
    dispatcher = NodeDispatcher(session)
    register_plugin_handlers(dispatcher, None)
    result = await dispatcher.handle_inbound(
        build_request("node.context.update", "1", {"sequence": 2, "locked": True})
    )
    assert result.ok
    stamp = session.context_received_at
    await dispatcher.handle_inbound(
        build_request("node.context.update", "2", {"sequence": 1, "locked": False})
    )
    assert session.context_state["locked"] is True
    assert session.context_received_at == stamp
    result = await dispatcher.handle_inbound(
        build_request(
            "node.context.update", "3", {"sequence": 3, "actor_account_key": "other"}
        )
    )
    assert not result.ok


async def test_plugin_call_uses_authenticated_actor_and_desktop_allowlist():
    registry = PluginServiceRegistry()
    handler = AsyncMock(return_value={"ok": True})
    registry.register(ServiceEntry("example", "v1", handler, desktop=True))
    registry.register(ServiceEntry("host", "private", handler))
    session = NodeSession(
        "session",
        "pc",
        state=NodeSessionState.ONLINE,
        actor_account_key="milky:user:123",
    )
    dispatcher = NodeDispatcher(session)
    register_plugin_handlers(dispatcher, registry)
    result = await dispatcher.handle_inbound(
        build_request(
            "plugin.service.call", "1", {"service": "example:v1", "operation": "list"}
        )
    )
    assert result.ok
    assert handler.call_args.args[2].actor_account_key == "milky:user:123"
    assert handler.call_args.args[2].node_id == "pc"
    assert not (
        await dispatcher.handle_inbound(
            build_request(
                "plugin.service.call",
                "2",
                {"service": "host:private", "operation": "send"},
            )
        )
    ).ok
    session.actor_account_key = ""
    assert not (
        await dispatcher.handle_inbound(
            build_request(
                "plugin.service.call",
                "3",
                {"service": "example:v1", "operation": "list"},
            )
        )
    ).ok


async def test_transport_filters_other_actors_and_expired_context():
    nodes = NodeRegistry()
    for name, actor in (("pc", "milky:user:123"), ("other-pc", "milky:user:456")):
        session = NodeSession("", name, actor_account_key=actor)
        nodes.register_session(
            session,
            node_id=name,
            display_name=name,
            node_type="desktop",
            capabilities=[NodeCapability(name="desktop.notification.deliver")],
            metadata={},
        )
        session.context_state = {"sequence": 1, "locked": False, "activity": "active"}
        session.context_received_at = time.monotonic() - 100
    invoker = SimpleNamespace(invoke=AsyncMock())
    service = DesktopDeliveryService(nodes, invoker)
    context = PluginServiceContext(
        "milky:user:123", caller_plugin_id="notification-router"
    )
    result = await service.handle("endpoints", {}, context)
    assert result == {"endpoints": [{"node_id": "pc", "state": {}, "fresh": False}]}
    result = await service.handle(
        "deliver",
        {
            "node_id": "other-pc",
            "notification_id": "id",
            "title": "today",
            "body": "private",
        },
        context,
    )
    assert not result["ok"]
    invoker.invoke.assert_not_called()
