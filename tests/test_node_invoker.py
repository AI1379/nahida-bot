"""Tests for NodeInvoker: capability.invoke round-trip and input submission."""

from __future__ import annotations

import asyncio

import pytest

from nahida_bot.gateway.node_protocol.errors import NodeInputUnavailable
from nahida_bot.gateway.node_protocol.schemas import NodeEnvelope, build_response
from nahida_bot.gateway.node_protocol.sessions import NodeSession
from nahida_bot.gateway.services.node_invoker import (
    NodeInputSink,
    NodeInvoker,
)
from nahida_bot.gateway.services.node_registry import NodeRegistry


class _FakeSink(NodeInputSink):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def submit(self, *, node_id: str, session_id: str, text: str) -> None:
        self.calls.append((node_id, session_id, text))


def _make_registered_session(
    registry: NodeRegistry,
    *,
    node_id: str,
    capabilities: list[str],
    request_handler,
) -> NodeSession:
    """Create an online session whose ``request`` callback simulates a node."""
    from nahida_bot.gateway.node_protocol.schemas import NodeCapability

    session = NodeSession(session_id="t", node_id=node_id)

    async def request(env: NodeEnvelope, timeout: float) -> NodeEnvelope:
        return await request_handler(env, timeout)

    session.request = request  # type: ignore[assignment]

    registry.register_session(
        session,
        node_id=node_id,
        display_name=node_id,
        node_type="desktop",
        capabilities=[NodeCapability(name=c) for c in capabilities],
        metadata={},
    )
    return session


@pytest.mark.asyncio
async def test_invoke_success_round_trip() -> None:
    registry = NodeRegistry()
    invoker = NodeInvoker(registry)

    async def handler(env: NodeEnvelope, timeout: float) -> NodeEnvelope:
        assert env.method == "capability.invoke"
        return build_response(env.id or "", ok=True, payload={"applied": True})

    _make_registered_session(
        registry,
        node_id="desktop-1",
        capabilities=["desktop.live2d.set_expression"],
        request_handler=handler,
    )

    result = await invoker.invoke(
        capability="desktop.live2d.set_expression",
        arguments={"expression": "happy"},
        caller="system",
    )

    assert result.ok is True
    assert result.payload == {"applied": True}
    assert result.audit is not None
    assert result.audit.ok is True
    assert result.audit.node_id == "desktop-1"
    assert result.audit.capability == "desktop.live2d.set_expression"
    assert len(invoker.audit_log) == 1


@pytest.mark.asyncio
async def test_invoke_missing_capability_returns_error() -> None:
    registry = NodeRegistry()
    invoker = NodeInvoker(registry)

    result = await invoker.invoke(capability="does.not.exist")

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "capability_not_found"
    assert len(invoker.audit_log) == 0


@pytest.mark.asyncio
async def test_invoke_node_error_is_propagated() -> None:
    registry = NodeRegistry()
    invoker = NodeInvoker(registry)

    async def handler(env: NodeEnvelope, timeout: float) -> NodeEnvelope:
        return build_response(
            env.id or "",
            ok=False,
            error={
                "code": "capability_local_denied",
                "message": "node allowlist rejected",
            },
        )

    _make_registered_session(
        registry,
        node_id="desktop-1",
        capabilities=["desktop.live2d.play_motion"],
        request_handler=handler,
    )

    result = await invoker.invoke(capability="desktop.live2d.play_motion")

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "capability_local_denied"
    assert result.audit is not None
    assert result.audit.error_code == "capability_local_denied"


@pytest.mark.asyncio
async def test_invoke_timeout_records_audit() -> None:
    registry = NodeRegistry()
    invoker = NodeInvoker(registry, default_timeout=0.05)

    async def handler(env: NodeEnvelope, timeout: float) -> NodeEnvelope:
        await asyncio.sleep(0.5)  # exceeds timeout
        return build_response(env.id or "", ok=True)

    _make_registered_session(
        registry,
        node_id="desktop-1",
        capabilities=["desktop.audio.play"],
        request_handler=handler,
    )

    result = await invoker.invoke(capability="desktop.audio.play")

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "capability_timeout"
    assert result.audit is not None
    assert result.audit.error_code == "capability_timeout"


@pytest.mark.asyncio
async def test_submit_node_input_delegates_to_sink() -> None:
    registry = NodeRegistry()
    sink = _FakeSink()
    invoker = NodeInvoker(registry, input_sink=sink)

    await invoker.submit_node_input(
        node_id="desktop-1", session_id="milky:private:10001", text="hello"
    )

    assert sink.calls == [("desktop-1", "milky:private:10001", "hello")]


@pytest.mark.asyncio
async def test_submit_node_input_without_sink_is_rejected() -> None:
    registry = NodeRegistry()
    invoker = NodeInvoker(registry, input_sink=None)

    with pytest.raises(NodeInputUnavailable, match="not configured"):
        await invoker.submit_node_input(node_id="desktop-1", session_id="s1", text="hi")


@pytest.mark.asyncio
async def test_invoke_explicit_node_id_targeting() -> None:
    registry = NodeRegistry()
    invoker = NodeInvoker(registry)

    async def handler(env: NodeEnvelope, timeout: float) -> NodeEnvelope:
        return build_response(env.id or "", ok=True, payload={"ok": True})

    _make_registered_session(
        registry,
        node_id="desktop-1",
        capabilities=["desktop.audio.play"],
        request_handler=handler,
    )
    _make_registered_session(
        registry,
        node_id="desktop-2",
        capabilities=["desktop.notification.show"],
        request_handler=handler,
    )

    result = await invoker.invoke(
        capability="desktop.audio.play",
        node_id="desktop-1",
    )
    assert result.ok is True

    wrong_owner = await invoker.invoke(
        capability="desktop.audio.play",
        node_id="desktop-2",
    )
    assert wrong_owner.ok is False
    assert wrong_owner.error is not None
    assert wrong_owner.error.code == "capability_not_found"

    # Explicit missing node.
    missing = await invoker.invoke(
        capability="desktop.audio.play", node_id="desktop-ghost"
    )
    assert missing.ok is False
    assert missing.error is not None
    assert missing.error.code == "capability_not_found"
