"""Channel registry — maps platform names to active ChannelPlugin instances."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from nahida_bot.plugins.channel_plugin import ChannelPlugin

logger = structlog.get_logger(__name__)


class ChannelRegistry:
    """Maps platform names (e.g. ``"telegram"``) to active ChannelPlugin instances.

    The MessageRouter uses this to route outbound responses back to the
    originating channel. PluginManager auto-registers/unregisters channels
    as they are enabled/disabled.
    """

    def __init__(self) -> None:
        self._channels: dict[str, ChannelPlugin] = {}

    def register(self, channel: ChannelPlugin) -> None:
        """Register an active channel plugin."""
        platform = channel.channel_id
        self._channels[platform] = channel
        logger.debug("channel_registry.registered", platform=platform)

    def unregister(self, platform: str) -> None:
        """Remove a channel by platform name."""
        popped = self._channels.pop(platform, None)
        if popped is not None:
            logger.debug("channel_registry.unregistered", platform=platform)

    def get(self, platform: str) -> ChannelPlugin | None:
        """Look up a channel by platform name."""
        return self._channels.get(platform)
