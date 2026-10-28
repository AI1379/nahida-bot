"""Channel delivery has one acknowledgement and lifecycle contract."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.context import SessionContext, current_session
from nahida_bot.core.events import EventBus, EventContext, MessageSent
from nahida_bot.core.outbound import (
    DeliveryError,
    outbound_payload,
    send_channel_message,
)
from nahida_bot.plugins.base import InboundMessage, OutboundMessage
from nahida_bot.plugins.conversation_joiner.plugin import ConversationJoinerPlugin
from nahida_bot.plugins.conversation_joiner.state import EngagementStateMachine


def _context():
    inbound = InboundMessage(
        message_id="trigger",
        platform="test",
        chat_id="g",
        user_id="human",
        text="hello",
        raw_event={},
        is_group=True,
    )
    return SessionContext(
        platform="test",
        chat_id="g",
        session_id="test:group:g:active",
        chat_address=ChatAddress("test", "group", "g"),
        inbound_message=inbound,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target,origin,notification",
    [
        ("g", "", False),
        ("another-group", "", False),
        ("g", "cron_trigger", False),
        ("g", "", True),
    ],
)
async def test_tool_reply_confirms_joiner_only_in_its_originating_chat(
    target, origin, notification
):
    context = replace(_context(), origin=origin)
    joiner = ConversationJoinerPlugin(
        api=SimpleNamespace(logger=Mock()),
        manifest=SimpleNamespace(config={"engagement": {"enabled": True}}),
    )
    joiner._sm = EngagementStateMachine(Mock())
    joiner._sm.transition_to_joining("test:group:g", 1.0)
    joiner._pending_requests["test:group:g"] = SimpleNamespace(
        session_id=context.session_id
    )
    bus = EventBus(EventContext(app=None, settings=None, logger=Mock()))
    sent = []

    async def capture(event, _):
        sent.append(event.payload)
        await joiner._on_message_sent(event)

    bus.subscribe(MessageSent, capture)
    channel = SimpleNamespace(send_message=AsyncMock(return_value="sent-1"))
    token = current_session.set(context)
    try:
        outbound = OutboundMessage(
            text="", extra={"chat_address": f"test:group:{target}"}, attachments=[]
        )
        message_id = await send_channel_message(
            {"test": channel},
            "test",
            target,
            outbound,
            event_bus=bus,
            payload=outbound_payload(
                target, "test", outbound, notification=notification
            ),
        )
    finally:
        current_session.reset(token)
        await bus.shutdown()
    assert message_id == "sent-1"
    assert len(sent) == 1
    if target == "g" and not origin and not notification:
        assert sent[0].message is context.inbound_message
        assert sent[0].session_id == context.session_id
        assert joiner._sm.get_state("test:group:g").state == "engaged"
    else:
        assert sent[0].delivery_kind == "notification"
        assert joiner._sm.get_state("test:group:g").state == "joining"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "empty", "exception"])
async def test_failed_delivery_does_not_confirm_or_cleanup(failure):
    channel = SimpleNamespace(send_message=AsyncMock(return_value=""))
    if failure == "exception":
        channel.send_message.side_effect = OSError("connection lost")
    registry = {} if failure == "missing" else {"test": channel}
    bus = SimpleNamespace(publish=AsyncMock())
    cleanup = AsyncMock()
    with pytest.raises((DeliveryError, OSError)):
        await send_channel_message(
            registry,
            "test",
            "g",
            OutboundMessage(text="hello"),
            event_bus=bus,
            cleanup=cleanup,
        )
    cleanup.assert_not_awaited()
    assert not any(
        isinstance(c.args[0], MessageSent) for c in bus.publish.await_args_list
    )


@pytest.mark.asyncio
async def test_post_send_observer_failure_cannot_turn_success_into_retry():
    bus = SimpleNamespace(
        publish=AsyncMock(side_effect=[None, RuntimeError("observer")])
    )
    channel = SimpleNamespace(send_message=AsyncMock(return_value="sent-1"))
    cleanup = AsyncMock(side_effect=OSError("cleanup"))
    assert (
        await send_channel_message(
            {"test": channel},
            "test",
            "g",
            OutboundMessage(text="hello"),
            event_bus=bus,
            cleanup=cleanup,
        )
        == "sent-1"
    )
    channel.send_message.assert_awaited_once()
    cleanup.assert_awaited_once()


def test_inbound_provenance_does_not_break_batch_identity_equality():
    context = _context()
    assert (
        replace(
            context, inbound_message=replace(context.inbound_message, message_id="next")
        )
        == context
    )
