"""Tests for the restricted semantic Desktop announcement service."""

from __future__ import annotations

import pytest

from nahida_bot.gateway.node_protocol.schemas import (
    NodeCapability,
    NodeEnvelope,
    build_response,
)
from nahida_bot.gateway.node_protocol.sessions import NodeSession
from nahida_bot.gateway.services.desktop_announcement import (
    DESKTOP_ANNOUNCE_CAPABILITY,
    DesktopAnnouncementService,
)
from nahida_bot.gateway.services.node_invoker import NodeInvoker
from nahida_bot.gateway.services.node_registry import NodeRegistry


def _register(
    registry: NodeRegistry,
    *,
    node_id: str,
    conversation_id: str,
    actor_account_key: str,
    calls: list[NodeEnvelope],
) -> None:
    session = NodeSession(
        session_id="pending",
        node_id=node_id,
        conversation_id=conversation_id,
        actor_account_key=actor_account_key,
    )

    async def request(envelope: NodeEnvelope, timeout: float) -> NodeEnvelope:
        calls.append(envelope)
        return build_response(envelope.id or "", ok=True, payload={"applied": True})

    session.request = request
    registry.register_session(
        session,
        node_id=node_id,
        display_name=node_id,
        node_type="desktop",
        capabilities=[NodeCapability(name=DESKTOP_ANNOUNCE_CAPABILITY)],
        metadata={},
    )


@pytest.mark.asyncio
async def test_announce_uses_actor_fallback_without_exposing_node_id() -> None:
    registry = NodeRegistry()
    calls: list[NodeEnvelope] = []
    _register(
        registry,
        node_id="desktop-owner",
        conversation_id="desktop:private:owner",
        actor_account_key="milky:user:owner",
        calls=calls,
    )
    service = DesktopAnnouncementService(registry, NodeInvoker(registry))

    result = await service.announce(
        message="  该休息一下了。  ",
        conversation_id="milky:private:owner",
        actor_account_key="milky:user:owner",
        caller="agent:cron:test",
    )

    assert result.ok is True
    assert result.node_id == "desktop-owner"
    assert calls[0].payload is not None
    assert calls[0].payload["capability"] == DESKTOP_ANNOUNCE_CAPABILITY
    assert calls[0].payload["arguments"] == {"message": "该休息一下了。"}


@pytest.mark.asyncio
async def test_announce_fails_closed_for_ambiguous_actor_binding() -> None:
    registry = NodeRegistry()
    calls: list[NodeEnvelope] = []
    for node_id in ("desktop-1", "desktop-2"):
        _register(
            registry,
            node_id=node_id,
            conversation_id=f"desktop:private:{node_id}",
            actor_account_key="milky:user:owner",
            calls=calls,
        )
    service = DesktopAnnouncementService(registry, NodeInvoker(registry))

    result = await service.announce(
        message="important",
        conversation_id="milky:private:owner",
        actor_account_key="milky:user:owner",
        caller="agent:cron:test",
    )

    assert result.ok is False
    assert result.error_code == "ambiguous_desktop"
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["", "x" * 301])
async def test_announce_rejects_invalid_message(message: str) -> None:
    registry = NodeRegistry()
    service = DesktopAnnouncementService(registry, NodeInvoker(registry))
    result = await service.announce(
        message=message,
        conversation_id="desktop:private:owner",
        actor_account_key="milky:user:owner",
        caller="agent:cron:test",
    )
    assert result.ok is False
    assert result.error_code == "invalid_arguments"
