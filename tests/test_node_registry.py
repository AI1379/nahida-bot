"""Tests for NodeRegistry: registration, duplicate displace, lookup, offline."""

from __future__ import annotations

import asyncio

import pytest

from nahida_bot.gateway.node_protocol.schemas import NodeEnvelope
from nahida_bot.gateway.node_protocol.schemas import NodeCapability
from nahida_bot.gateway.node_protocol.sessions import (
    NodeSession,
    NodeSessionState,
)
from nahida_bot.gateway.services.node_registry import NodeRegistry


def _caps(*names: str) -> list[NodeCapability]:
    return [NodeCapability(name=n) for n in names]


def test_register_session_makes_node_online() -> None:
    registry = NodeRegistry()
    session = NodeSession(session_id="temp", node_id="desktop-1")

    registry.register_session(
        session,
        node_id="desktop-1",
        display_name="Desktop",
        node_type="desktop",
        capabilities=_caps("desktop.live2d.set_expression"),
        metadata={},
    )

    assert session.state == NodeSessionState.ONLINE
    assert session.node_id == "desktop-1"
    online = registry.get_online_session("desktop-1")
    assert online is session
    assert online.get_capability("desktop.live2d.set_expression") is not None


def test_find_capability_owner_returns_online_session() -> None:
    registry = NodeRegistry()
    s1 = NodeSession(session_id="t", node_id="n1")
    s2 = NodeSession(session_id="t", node_id="n2")
    registry.register_session(
        s1,
        node_id="n1",
        display_name="a",
        node_type="desktop",
        capabilities=_caps("desktop.audio.play"),
        metadata={},
    )
    registry.register_session(
        s2,
        node_id="n2",
        display_name="b",
        node_type="desktop",
        capabilities=_caps("desktop.notification.show"),
        metadata={},
    )

    assert registry.find_capability_owner("desktop.audio.play") is s1
    assert registry.find_capability_owner("desktop.notification.show") is s2
    assert registry.find_capability_owner("missing.cap") is None


def test_find_bound_capability_owners_prefers_conversation_then_actor() -> None:
    registry = NodeRegistry()
    exact = NodeSession(
        session_id="t",
        node_id="exact",
        conversation_id="desktop:private:owner",
        actor_account_key="milky:user:owner",
    )
    actor = NodeSession(
        session_id="t",
        node_id="actor",
        conversation_id="desktop:private:other",
        actor_account_key="milky:user:owner",
    )
    for session in (exact, actor):
        registry.register_session(
            session,
            node_id=session.node_id,
            display_name=session.node_id,
            node_type="desktop",
            capabilities=_caps("desktop.notification.announce"),
            metadata={},
        )

    assert registry.find_bound_capability_owners(
        capability="desktop.notification.announce",
        conversation_id="desktop:private:owner",
        actor_account_key="milky:user:owner",
    ) == [exact]
    assert registry.find_bound_capability_owners(
        capability="desktop.notification.announce",
        conversation_id="milky:private:owner",
        actor_account_key="milky:user:owner",
    ) == [exact, actor]


def test_duplicate_node_id_displaces_old_session() -> None:
    registry = NodeRegistry()
    old = NodeSession(session_id="t", node_id="desktop-1")
    registry.register_session(
        old,
        node_id="desktop-1",
        display_name="old",
        node_type="desktop",
        capabilities=_caps("desktop.live2d.set_expression"),
        metadata={},
    )

    new = NodeSession(session_id="t", node_id="desktop-1")
    registry.register_session(
        new,
        node_id="desktop-1",
        display_name="new",
        node_type="desktop",
        capabilities=_caps("desktop.live2d.set_expression"),
        metadata={},
    )

    assert old.state == NodeSessionState.OFFLINE
    assert new.state == NodeSessionState.ONLINE
    assert registry.get_online_session("desktop-1") is new


def test_mark_offline_clears_node_index() -> None:
    registry = NodeRegistry()
    session = NodeSession(session_id="t", node_id="desktop-1")
    registry.register_session(
        session,
        node_id="desktop-1",
        display_name="d",
        node_type="desktop",
        capabilities=[],
        metadata={},
    )
    assert registry.get_online_session("desktop-1") is session

    registry.mark_offline(session)

    assert session.state == NodeSessionState.OFFLINE
    assert registry.get_online_session("desktop-1") is None
    assert registry.get_session(session.session_id) is None


def test_list_online_nodes_returns_summaries() -> None:
    registry = NodeRegistry()
    s = NodeSession(session_id="t", node_id="desktop-1")
    registry.register_session(
        s,
        node_id="desktop-1",
        display_name="Desktop",
        node_type="desktop",
        capabilities=_caps("desktop.live2d.set_expression"),
        metadata={"platform": "windows"},
    )

    summaries = registry.list_online_nodes()
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary["node_id"] == "desktop-1"
    assert summary["state"] == "online"
    assert summary["online"] is True
    assert isinstance(summary["capabilities"], list)


def test_state_summary_update_and_get() -> None:
    registry = NodeRegistry()
    registry.update_state_summary("desktop-1", {"window": {"mode": "emerged"}})
    assert registry.get_state_summary("desktop-1") == {"window": {"mode": "emerged"}}
    assert registry.get_state_summary("unknown") is None


@pytest.mark.asyncio
async def test_duplicate_connection_notifies_then_closes_old_transport() -> None:
    registry = NodeRegistry()
    old = NodeSession(session_id="t", node_id="desktop-1")
    sent: list[NodeEnvelope] = []
    closed: list[tuple[int, str]] = []
    close_finished = asyncio.Event()

    async def send(envelope: NodeEnvelope) -> None:
        sent.append(envelope)

    async def close(code: int, reason: str) -> None:
        closed.append((code, reason))
        close_finished.set()

    old.send = send
    old.close = close
    registry.register_session(
        old,
        node_id="desktop-1",
        display_name="old",
        node_type="desktop",
        capabilities=[],
        metadata={},
    )

    registry.register_session(
        NodeSession(session_id="t", node_id="desktop-1"),
        node_id="desktop-1",
        display_name="new",
        node_type="desktop",
        capabilities=[],
        metadata={},
    )

    await asyncio.wait_for(close_finished.wait(), timeout=1.0)
    assert sent[0].event == "node.duplicate_connection"
    assert closed == [(4001, "duplicate node connection")]


@pytest.mark.asyncio
async def test_disconnect_node_removes_and_closes_online_session() -> None:
    registry = NodeRegistry()
    session = NodeSession(session_id="t", node_id="desktop-1")
    closed: list[tuple[int, str]] = []

    async def close(code: int, reason: str) -> None:
        closed.append((code, reason))

    session.close = close
    registry.register_session(
        session,
        node_id="desktop-1",
        display_name="Desktop",
        node_type="desktop",
        capabilities=[],
        metadata={},
    )

    assert await registry.disconnect_node("desktop-1", reason="revoked") is True
    assert registry.get_online_session("desktop-1") is None
    assert closed == [(4003, "revoked")]
    assert await registry.disconnect_node("desktop-1", reason="again") is False
