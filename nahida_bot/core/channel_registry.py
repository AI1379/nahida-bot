"""Channel registry — maps platform names to active channel services."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from nahida_bot.plugins.base import ChannelService

logger = structlog.get_logger(__name__)


class ChannelRegistry:
    """Maps platform names (e.g. ``"telegram"``) to active channel services.

    The MessageRouter uses this to route outbound responses back to the
    originating channel. Services are registered explicitly by plugins through
    ``BotAPI.register_channel()``.
    """

    def __init__(self) -> None:
        self._channels: dict[str, ChannelService] = {}

    def register(self, channel: ChannelService) -> None:
        """Register an active channel service."""
        platform = channel.channel_id
        self._channels[platform] = channel
        logger.debug("channel_registry.registered", platform=platform)

    def unregister(self, platform: str) -> None:
        """Remove a channel by platform name."""
        popped = self._channels.pop(platform, None)
        if popped is not None:
            logger.debug("channel_registry.unregistered", platform=platform)

    def get(self, platform: str) -> ChannelService | None:
        """Look up a channel by platform name."""
        return self._channels.get(platform)
