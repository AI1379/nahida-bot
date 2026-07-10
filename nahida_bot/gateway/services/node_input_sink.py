"""Bridge node-originated text input into the core message pipeline."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from uuid import uuid4

from nahida_bot.core.chat_address import SessionKey
from nahida_bot.core.events import MessagePayload, MessageReceived
from nahida_bot.gateway.node_protocol.errors import NodeInputUnavailable
from nahida_bot.plugins.base import (
    ChatContext,
    InboundMessage,
    MessageContext,
    SenderContext,
)

if TYPE_CHECKING:
    from nahida_bot.core.app import Application


class ApplicationNodeInputSink:
    """Publish validated node input as a standard ``MessageReceived`` event."""

    def __init__(self, application: Application) -> None:
        self._application = application

    async def submit(self, *, node_id: str, session_id: str, text: str) -> None:
        router = self._application.message_router
        if router is None:
            raise NodeInputUnavailable("message router is not initialized")

        clean_text = text.strip()
        if not clean_text:
            raise ValueError("node input text must not be empty")

        try:
            session_key = SessionKey.parse(session_id)
        except ValueError as exc:
            raise ValueError(f"invalid node input session_id: {exc}") from exc
        address = session_key.address
        if not address.is_typed:
            raise ValueError("node input session_id must use a typed chat address")
        if self._application.channel_registry.get(address.channel) is None:
            raise NodeInputUnavailable(
                f"channel {address.channel!r} is not connected for node input"
            )

        now = time.time()
        source_user_id = f"node:{node_id}"
        inbound = InboundMessage(
            message_id=f"node_{uuid4().hex}",
            platform=address.channel,
            chat_id=address.target_id,
            user_id=source_user_id,
            text=clean_text,
            raw_event={
                "source": "node",
                "node_id": node_id,
                "session_id": session_id,
            },
            is_group=address.target_type == "group",
            timestamp=now,
            sender_context=SenderContext(
                display_name=f"Node {node_id}",
                platform_user_id=source_user_id,
                role_tags=("node",),
            ),
            chat_context=ChatContext(
                platform=address.channel,
                chat_type=address.target_type,
                platform_chat_id=address.target_id,
            ),
            message_context=MessageContext(
                timestamp=now,
                channel=address.channel,
                chat_type=address.target_type,
                chat_id=address.target_id,
                sender_id=source_user_id,
                sender_display_name=f"Node {node_id}",
                sender_role_tags=("node",),
                extra_tags=("source:node", f"node_id:{node_id}"),
            ),
        )
        result = await self._application.event_bus.publish(
            MessageReceived(
                payload=MessagePayload(message=inbound, session_id=session_id),
                source=f"node:{node_id}",
            )
        )
        if result.failures:
            details = "; ".join(
                f"{failure.handler_name}: {failure.error}"
                for failure in result.failures
            )
            raise NodeInputUnavailable(f"node input dispatch failed: {details}")


__all__ = ["ApplicationNodeInputSink"]
