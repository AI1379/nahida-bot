"""Tests for the node protocol dispatcher: request routing, response matching,
and the pre-registration guard.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from nahida_bot.gateway.node_protocol.dispatcher import (
    InboundHandlerResult,
    NodeDispatcher,
)
from nahida_bot.gateway.node_protocol.errors import MethodNotFound
from nahida_bot.gateway.node_protocol.routes import _request_round_trip
from nahida_bot.gateway.node_protocol.schemas import (
    build_request,
    build_response,
)
from nahida_bot.gateway.node_protocol.sessions import NodeSession, NodeSessionState


def _make_session(
    state: NodeSessionState = NodeSessionState.REGISTERING,
) -> NodeSession:
    return NodeSession(session_id="s1", node_id="desktop-1", state=state)


@pytest.mark.asyncio
async def test_unregistered_session_rejects_non_register_requests() -> None:
    session = _make_session(NodeSessionState.AUTHENTICATING)
    dispatcher = NodeDispatcher(session)

    request = build_request("capability.invoke", request_id="r1", payload={})
    response = await dispatcher.handle_inbound(request)

    assert response is not None
    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "not_registered"


@pytest.mark.asyncio
async def test_unknown_method_returns_method_not_found() -> None:
    session = _make_session(NodeSessionState.ONLINE)
    dispatcher = NodeDispatcher(session)

    request = build_request("totally.unknown", request_id="r2")
    response = await dispatcher.handle_inbound(request)

    assert response is not None
    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "method_not_found"


@pytest.mark.asyncio
async def test_register_request_rejected_after_online() -> None:
    session = _make_session(NodeSessionState.ONLINE)
    dispatcher = NodeDispatcher(session)

    async def handler(sess: NodeSession, envelope):
        return InboundHandlerResult(ok=True)

    dispatcher.register_inbound_handler("node.register", handler)

    request = build_request(
        "node.register",
        request_id="r2b",
        payload={"node_id": "desktop-1"},
    )
    response = await dispatcher.handle_inbound(request)

    assert response is not None
    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "register_rejected"


@pytest.mark.asyncio
async def test_offline_session_rejects_requests() -> None:
    session = _make_session(NodeSessionState.OFFLINE)
    dispatcher = NodeDispatcher(session)
    dispatcher.register_inbound_handler(
        "node.register", lambda s, e: InboundHandlerResult(ok=True)
    )

    request = build_request(
        "node.register",
        request_id="r2c",
        payload={"node_id": "desktop-1"},
    )
    response = await dispatcher.handle_inbound(request)

    assert response is not None
    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "not_registered"


@pytest.mark.asyncio
async def test_registered_handler_receives_request_and_returns_response() -> None:
    session = _make_session()
    dispatcher = NodeDispatcher(session)

    seen: dict[str, object] = {}

    async def handler(sess: NodeSession, envelope):
        seen["method"] = envelope.method
        seen["node_id"] = sess.node_id
        return InboundHandlerResult(ok=True, payload={"echo": True})

    dispatcher.register_inbound_handler("node.register", handler)

    request = build_request(
        "node.register",
        request_id="r3",
        payload={"node_id": "desktop-1"},
    )
    response = await dispatcher.handle_inbound(request)

    assert response is not None
    assert response.ok is True
    assert response.payload == {"echo": True}
    assert seen["method"] == "node.register"
    assert seen["node_id"] == "desktop-1"


@pytest.mark.asyncio
async def test_handler_exception_surfaces_as_error_response() -> None:
    session = _make_session()
    dispatcher = NodeDispatcher(session)

    async def handler(sess: NodeSession, envelope):
        raise MethodNotFound("nope")

    dispatcher.register_inbound_handler("node.register", handler)

    request = build_request("node.register", request_id="r4", payload={"node_id": "n"})
    response = await dispatcher.handle_inbound(request)

    assert response is not None
    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "method_not_found"


@pytest.mark.asyncio
async def test_invalid_payload_returns_invalid_arguments() -> None:
    session = _make_session()
    dispatcher = NodeDispatcher(session)
    dispatcher.register_inbound_handler(
        "node.register", lambda s, e: InboundHandlerResult(ok=True)
    )

    # Missing required node_id field.
    request = build_request("node.register", request_id="r5", payload={})
    response = await dispatcher.handle_inbound(request)

    assert response is not None
    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "invalid_arguments"


@pytest.mark.asyncio
async def test_outbound_request_response_matching() -> None:
    """An outbound request awaited via the dispatcher resolves when the
    matching response frame arrives through ``handle_inbound``."""
    session = _make_session()
    dispatcher = NodeDispatcher(session)

    request = build_request("capability.invoke", request_id="req_out_1", payload={})

    async def sender() -> NodeSession:
        # Simulate the node replying after the request is registered.
        await asyncio.sleep(0.01)
        response = build_response("req_out_1", ok=True, payload={"applied": True})
        await dispatcher.handle_inbound(response)
        return session

    asyncio.create_task(sender())
    result = await dispatcher.await_response(request, timeout=1.0)

    assert result.ok is True
    assert result.payload == {"applied": True}


@pytest.mark.asyncio
async def test_request_round_trip_registers_pending_before_send() -> None:
    """A response arriving during send must not be dropped as unknown."""
    session = _make_session(NodeSessionState.ONLINE)
    dispatcher = NodeDispatcher(session)
    request = build_request("capability.invoke", request_id="req_fast", payload={})

    class FastReplyWebSocket:
        async def send_text(self, text: str) -> None:
            sent = json.loads(text)
            await dispatcher.handle_inbound(
                build_response(sent["id"], ok=True, payload={"fast": True})
            )

    result = await _request_round_trip(
        FastReplyWebSocket(),  # type: ignore[arg-type]
        dispatcher,
        request,
        timeout=1.0,
    )

    assert result.ok is True
    assert result.payload == {"fast": True}


@pytest.mark.asyncio
async def test_outbound_request_timeout() -> None:
    session = _make_session()
    dispatcher = NodeDispatcher(session)

    request = build_request("capability.invoke", request_id="req_out_2", payload={})
    with pytest.raises(TimeoutError):
        await dispatcher.await_response(request, timeout=0.05)


@pytest.mark.asyncio
async def test_unknown_response_id_is_ignored() -> None:
    session = _make_session()
    dispatcher = NodeDispatcher(session)

    stray = build_response("does_not_exist", ok=True)
    # Should not raise and should not crash.
    result = await dispatcher.handle_inbound(stray)
    assert result is None


@pytest.mark.asyncio
async def test_cancel_pending_resolves_future_with_error() -> None:
    session = _make_session()
    dispatcher = NodeDispatcher(session)

    request = build_request("capability.invoke", request_id="req_out_3", payload={})
    task = asyncio.create_task(dispatcher.await_response(request, timeout=5.0))
    await asyncio.sleep(0)  # let the pending registration happen

    assert dispatcher.cancel_pending("req_out_3", reason="killed")

    result = await task
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "capability_cancelled"


@pytest.mark.asyncio
async def test_cancel_all_pending_on_session_close() -> None:
    session = _make_session()
    dispatcher = NodeDispatcher(session)

    r1 = build_request("capability.invoke", request_id="req_a", payload={})
    r2 = build_request("capability.invoke", request_id="req_b", payload={})
    t1 = asyncio.create_task(dispatcher.await_response(r1, timeout=5.0))
    t2 = asyncio.create_task(dispatcher.await_response(r2, timeout=5.0))
    await asyncio.sleep(0)

    dispatcher.cancel_all_pending(reason="session closed")

    for task in (t1, t2):
        result = await task
        assert result.ok is False
        assert result.error is not None
        assert result.error.code == "capability_cancelled"


@pytest.mark.asyncio
async def test_heartbeat_and_event_frames_do_not_echo() -> None:
    session = _make_session()
    dispatcher = NodeDispatcher(session)

    from nahida_bot.gateway.node_protocol.schemas import build_event, build_heartbeat

    assert await dispatcher.handle_inbound(build_heartbeat("ping")) is None
    assert (
        await dispatcher.handle_inbound(build_event("node.state.updated", payload={}))
        is None
    )
