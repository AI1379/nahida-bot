"""Turn identity/routing boundary tests for issue #7."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.context import SessionContext
from nahida_bot.core.router import MessageRouter
from nahida_bot.identity.models import IdentityResolution
from nahida_bot.plugins.base import InboundMessage, SenderContext


def test_session_context_keeps_legacy_conversation_fallback() -> None:
    ctx = SessionContext(
        platform="desktop",
        chat_id="local-user",
        session_id="legacy-session",
    )

    assert ctx.effective_conversation_id == "legacy-session"
    assert ctx.actor_account_key == ""


def test_session_context_prefers_explicit_conversation_identity() -> None:
    ctx = SessionContext(
        platform="desktop",
        chat_id="local-user",
        session_id="legacy-session",
        conversation_id="conversation:owner-desktop",
        sender_account_key="desktop:user:owner",
        transport_address="node:desktop-local",
        reply_route="node:desktop-local",
        credential_id="node-token:desktop-local",
    )

    assert ctx.effective_conversation_id == "conversation:owner-desktop"
    assert ctx.actor_account_key == "desktop:user:owner"
    assert ctx.transport_address == "node:desktop-local"
    assert ctx.reply_route == "node:desktop-local"
    assert ctx.credential_id == "node-token:desktop-local"


@pytest.mark.asyncio
async def test_channel_turn_projects_explicit_identity_and_routing_fields() -> None:
    resolver = MagicMock()
    resolver.resolve = AsyncMock(
        return_value=IdentityResolution(
            chat_address="milky:private:10001",
            session_id="milky:private:10001",
            sender_account_key="milky:user:10001",
            person_id="owner",
            confidence="linked",
            source="config_seed",
        )
    )
    router = MessageRouter(
        event_bus=MagicMock(),
        command_registry=MagicMock(),
        command_matcher=MagicMock(),
        channel_registry=MagicMock(),
        runner=None,
        workspace_manager=None,
        config=None,
        identity_resolver=resolver,
    )
    inbound = InboundMessage(
        message_id="m1",
        platform="milky",
        chat_id="10001",
        user_id="10001",
        text="hello",
        raw_event={},
        sender_context=SenderContext(platform_user_id="10001"),
    )
    address = ChatAddress(
        channel="milky",
        target_type="private",
        target_id="10001",
    )

    ctx = await router._build_session_context(
        inbound,
        address,
        "milky:private:10001",
        "default",
    )

    assert ctx.transport_address == "milky:private:10001"
    assert ctx.conversation_id == "milky:private:10001"
    assert ctx.reply_route == "milky:private:10001"
    assert ctx.credential_id == "milky:user:10001"
    assert ctx.actor_account_key == "milky:user:10001"
    assert ctx.person_id == "owner"
