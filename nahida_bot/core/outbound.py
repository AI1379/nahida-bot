"""Shared channel delivery acknowledgements and message lifecycle events."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import structlog

from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.context import current_session
from nahida_bot.core.events import MessagePayload, MessageSending, MessageSent
from nahida_bot_sdk.messaging import (
    ChatContext,
    InboundMessage,
    OutboundMessage,
    SenderContext,
)

if TYPE_CHECKING:
    from nahida_bot.core.events import EventBus

logger = structlog.get_logger(__name__)


class DeliveryError(RuntimeError):
    """The channel was unavailable or did not acknowledge delivery."""


def outbound_payload(
    target: str, channel: str, message: OutboundMessage, *, notification: bool = False
) -> MessagePayload:
    """Correlate tool replies with their real inbound turn; isolate notifications."""
    context = current_session.get()
    raw_address = message.extra.get("chat_address")
    if isinstance(raw_address, str) and raw_address:
        address = ChatAddress.parse(raw_address)
    elif (
        context is not None
        and context.platform == channel
        and context.chat_id == target
    ):
        address = context.chat_address or ChatAddress.from_inbound(channel, target)
    else:
        address = ChatAddress.from_inbound(channel, target)

    same_chat = bool(
        context is not None
        and context.platform == address.channel
        and context.chat_id == address.target_id
        and (context.chat_address is None or context.chat_address == address)
    )
    if (
        same_chat
        and context is not None
        and context.inbound_message is not None
        and not notification
        and context.origin != "cron_trigger"
    ):
        return MessagePayload(
            message=context.inbound_message,
            session_id=context.session_id,
            outbound=message,
            conversation_id=context.effective_conversation_id,
            transport_address=context.transport_address,
            reply_route=context.reply_route,
            credential_id=context.credential_id,
            actor_account_key=context.actor_account_key,
        )

    # No originating user message: this is a notification, not another user's turn.
    return MessagePayload(
        message=InboundMessage(
            message_id="",
            platform=address.channel,
            chat_id=address.target_id,
            user_id="",
            text="",
            raw_event={},
            is_group=address.target_type == "group",
            chat_context=ChatContext(
                platform=address.channel, chat_type=address.target_type
            ),
            sender_context=SenderContext(display_name="bot", is_self=True, is_bot=True),
        ),
        session_id=(
            context.session_id
            if same_chat and context is not None
            else address.chat_key
        ),
        outbound=message,
        delivery_kind="notification",
    )


async def send_channel_message(
    registry: Any,
    channel: str,
    target: str,
    message: OutboundMessage,
    *,
    event_bus: EventBus | None = None,
    payload: MessagePayload | None = None,
    source: str = "channel_delivery",
    cleanup: Callable[[OutboundMessage], Awaitable[None]] | None = None,
) -> str:
    """Send once; only an acknowledged send emits MessageSent or consumes files.

    Missing routes, exceptions and empty acknowledgements never report success.
    Failures in post-send observers/cleanup cannot turn an accepted send into a
    retryable failure and duplicate its external side effect.
    """
    service = registry.get(channel) if registry is not None and channel else None
    if service is None:
        raise DeliveryError(f"Channel {channel!r} is unavailable; message was not sent")
    payload = (
        replace(payload, outbound=message)
        if payload is not None
        else outbound_payload(target, channel, message)
    )
    if event_bus is not None:
        await event_bus.publish(MessageSending(payload=payload, source=source))
    message_id = await service.send_message(target, message)
    if not isinstance(message_id, str) or not message_id.strip():
        raise DeliveryError(
            "Channel did not acknowledge delivery; do not blindly retry"
        )
    if event_bus is not None:
        try:
            await event_bus.publish(MessageSent(payload=payload, source=source))
        except Exception:
            logger.exception(
                "outbound.sent_observer_failed", channel=channel, message_id=message_id
            )
    if cleanup is not None:
        try:
            await cleanup(message)
        except Exception:
            logger.exception(
                "outbound.cleanup_failed", channel=channel, message_id=message_id
            )
    return message_id
