"""Shared chat-context helpers for builtin tools and commands."""

from __future__ import annotations

from typing import Any

from nahida_bot.core.chat_address import ChatAddress
from nahida_bot_sdk.messaging import InboundMessage


def address_from_inbound(inbound: InboundMessage) -> ChatAddress:
    """Resolve a typed address from normalized inbound metadata when possible."""
    chat_type = ""
    if inbound.chat_context and inbound.chat_context.chat_type:
        chat_type = inbound.chat_context.chat_type
    elif inbound.message_context and inbound.message_context.chat_type:
        chat_type = inbound.message_context.chat_type
    return ChatAddress.from_inbound(
        inbound.platform,
        inbound.chat_id,
        is_group=inbound.is_group,
        chat_type=chat_type,
    )


def address_from_session_context(context: Any) -> ChatAddress:
    """Resolve a chat address from a session context with legacy fallback."""
    address = getattr(context, "chat_address", None)
    if isinstance(address, ChatAddress):
        return address
    return ChatAddress.from_inbound(
        str(getattr(context, "platform", "")),
        str(getattr(context, "chat_id", "")),
    )


def typed_address_from_session_context(context: Any) -> ChatAddress | None:
    """Return the session address only when its chat type is explicit."""
    address = address_from_session_context(context)
    return address if address.is_typed else None


def job_matches_address(job: Any, address: ChatAddress) -> bool:
    """Return whether a scheduled job belongs to a typed chat address."""
    return (
        address.is_typed
        and job.platform == address.channel
        and job.chat_id == address.target_id
        and job.chat_type == address.target_type
    )


def job_visible_to_user(job: Any, address: ChatAddress, user_id: str) -> bool:
    """Apply chat and creator ownership filtering to a scheduled job."""
    if not job_matches_address(job, address):
        return False
    # Group admins may eventually need a controlled way to manage every job in
    # a group. Until then, creator filtering avoids accidental cross-user edits.
    owner = str(getattr(job, "created_by_user_id", "") or "")
    if not owner:
        return True
    return bool(user_id) and owner == user_id
