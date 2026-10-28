"""Shared chat-context helpers for builtin tools and commands."""

from __future__ import annotations

from typing import Any

from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.chat_address import (
    address_from_inbound as address_from_inbound,
)
from nahida_bot.core.chat_address import (
    address_from_session_context as address_from_session_context,
)
from nahida_bot.core.chat_address import (
    typed_address_from_session_context as typed_address_from_session_context,
)


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
