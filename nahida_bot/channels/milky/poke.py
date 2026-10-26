"""Current-chat outbound pokes; response policy stays with the agent/joiner."""

from __future__ import annotations

import asyncio
import math
import re
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable

from nahida_bot.channels.milky.client import MilkyClient, MilkyClientError
from nahida_bot.channels.milky.config import MilkyPluginConfig
from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.context import current_session
from nahida_bot.plugins.base import BotAPI


class MilkyPokeTool:
    """Validate the current destination and reserve cooldown before sending."""

    def __init__(
        self,
        *,
        api: BotAPI,
        channel_id: str,
        config: MilkyPluginConfig,
        get_client: Callable[[], MilkyClient],
        get_self_id: Callable[[], int],
        is_group_member: Callable[[MilkyClient, int, int], Awaitable[bool]],
    ) -> None:
        self._api = api
        self._channel_id = channel_id
        self._config = config
        self._get_client = get_client
        self._get_self_id = get_self_id
        self._is_group_member = is_group_member
        self._last_poke: OrderedDict[str, float] = OrderedDict()
        self._lock = asyncio.Lock()

    def register(self) -> None:
        """Expose an explicit tool, with no keyword-triggered side effects."""
        self._api.register_tool(
            "milky_poke_user",
            "Poke one QQ user once in the current Milky chat when a light social "
            "interaction fits the conversation. Omit user_id to poke the sender. "
            "In groups, use a real member ID from context; in private chats only "
            "the current peer is allowed. Respect cooldown errors; do not retry "
            "a failed or uncertain send. This sends the action immediately.",
            {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "pattern": "^[0-9]+$",
                        "description": "QQ user ID; defaults to the current sender.",
                    }
                },
                "additionalProperties": False,
            },
            self.send,
        )

    async def send(self, user_id: str = "") -> str:
        """Send only to a validated peer/member of the typed current chat."""
        try:
            address, sender = self._current_address()
            peer_id, target_id = self._target(user_id.strip() or sender, address)
        except ValueError as exc:
            return f"Error: {exc}"
        client = self._get_client()
        is_group = address.target_type == "group"
        if is_group and not await self._is_group_member(client, peer_id, target_id):
            return "Error: Could not verify that the target belongs to this group."
        async with self._lock:
            try:
                self._reserve_cooldown(address.chat_key)
            except ValueError as exc:
                return f"Error: {exc}"
        try:
            if is_group:
                await client.send_group_nudge(group_id=peer_id, user_id=target_id)
            else:
                await client.send_friend_nudge(user_id=target_id)
        except MilkyClientError as exc:
            self._api.logger.warning(
                "milky.poke_outbound_failed",
                chat_address=address.chat_key,
                user_id=str(target_id),
                error=str(exc),
            )
            return (
                "Error: Poke failed or delivery is uncertain; do not retry this turn."
            )
        self._api.logger.info(
            "milky.poke_outbound", chat_address=address.chat_key, user_id=str(target_id)
        )
        return f"Poked QQ user {target_id} once in {address.chat_key}."

    def _current_address(self) -> tuple[ChatAddress, str]:
        """Use explicit runtime routing, never a destination supplied by the model."""
        if not self._config.enable_poke_tool:
            raise ValueError("Milky poke tool is disabled.")
        context = current_session.get()
        if context is None or context.platform != self._channel_id:
            raise ValueError("Poking requires an active Milky session.")
        address = context.chat_address
        if (
            address is None
            or address.channel != self._channel_id
            or address.target_type not in {"private", "group"}
            or address.target_id != context.chat_id
        ):
            raise ValueError("Poking requires a typed address for the current chat.")
        return address, context.user_id

    def _target(self, target: str, address: ChatAddress) -> tuple[int, int]:
        """Validate QQ identity, allowlists, and private-peer confinement."""
        peer_id, target_id = _qq_id(address.target_id), _qq_id(target)
        self_id = self._get_self_id()
        if self_id <= 0:
            raise ValueError("Bot identity is not available yet.")
        if target_id == self_id:
            raise ValueError("Cannot poke the bot itself.")
        is_group = address.target_type == "group"
        allowed = (
            self._config.allowed_groups if is_group else self._config.allowed_friends
        )
        if allowed and address.target_id not in allowed:
            raise ValueError("This chat is not allowed by the Milky configuration.")
        if not is_group and target_id != peer_id:
            raise ValueError("Private pokes can only target the current peer.")
        return peer_id, target_id

    def _reserve_cooldown(self, chat_key: str) -> None:
        """Called under the lock; failed/uncertain sends keep the reservation."""
        now = time.monotonic()
        previous = self._last_poke.get(chat_key)
        if previous is not None:
            remaining = self._config.poke_cooldown_seconds - (now - previous)
            if remaining > 0:
                raise ValueError(f"Poke cooldown; wait {math.ceil(remaining)} seconds.")
        self._last_poke[chat_key] = now
        self._last_poke.move_to_end(chat_key)
        while len(self._last_poke) > 4096:
            self._last_poke.popitem(last=False)


def _qq_id(value: str) -> int:
    """Reject signs, Unicode digits, zero and out-of-range protocol values."""
    if not re.fullmatch(r"[0-9]{1,19}", value):
        raise ValueError("QQ user and chat IDs must be positive integers.")
    number = int(value)
    if not 0 < number < 2**63:
        raise ValueError("QQ user and chat IDs must be positive int64 values.")
    return number
