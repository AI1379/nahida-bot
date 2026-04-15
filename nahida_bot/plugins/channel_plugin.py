"""ChannelPlugin base class for external platform integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nahida_bot.plugins.base import OutboundMessage, Plugin

if TYPE_CHECKING:
    from nahida_bot.plugins.manifest import PluginManifest
    from nahida_bot.plugins.base import BotAPI


class ChannelPlugin(Plugin):
    """Base class for plugins that connect the bot to external messaging platforms.

    Subclass this to implement a specific platform adapter (Telegram, QQ/Discord,
    etc.). The plugin system treats channels like any other plugin, so they
    benefit from the same manifest, permissions, lifecycle, and isolation
    guarantees.

    Communication mode flags declare which transport mechanisms this channel
    uses. The plugin host may use these flags to auto-register webhook routes,
    WebSocket endpoints, etc. in future phases.
    """

    # ── Communication mode flags ───────────────────────
    # Override these in subclasses to declare supported transports.

    SUPPORT_HTTP_SERVER: bool = False  # Bot exposes an HTTP endpoint (webhook)
    SUPPORT_HTTP_CLIENT: bool = False  # Bot makes outbound HTTP requests
    SUPPORT_WEBSOCKET_SERVER: bool = False  # Bot exposes a WebSocket endpoint
    SUPPORT_WEBSOCKET_CLIENT: bool = False  # Bot connects to an external WebSocket
    SUPPORT_SSE: bool = False  # Bot pushes events via SSE

    def __init__(self, api: BotAPI, manifest: PluginManifest) -> None:
        super().__init__(api, manifest)
        self._channel_id: str = manifest.id

    @property
    def channel_id(self) -> str:
        """Unique identifier for this channel (same as plugin id)."""
        return self._channel_id

    async def handle_inbound_event(self, event: dict[str, Any]) -> None:
        """Process a raw platform event.

        Subclasses must:
        1. Parse the platform-native ``event`` dict.
        2. Create an ``InboundMessage``.
        3. Publish a ``MessageReceived`` event on the event bus.

        For group messages, the subclass is responsible for stripping @mention
        prefixes before setting ``InboundMessage.text`` so that downstream
        command matching works correctly.
        """
        raise NotImplementedError

    async def send_message(self, target: str, message: OutboundMessage) -> str:
        """Send a message to a platform target. Returns the platform message ID.

        Args:
            target: Platform-specific identifier (user ID, group ID, etc.).
            message: The standardized outbound message.

        Returns:
            Platform-assigned message ID, or empty string if unavailable.
        """
        raise NotImplementedError

    async def get_user_info(self, user_id: str) -> dict[str, Any]:
        """Fetch user profile from the platform. Optional override."""
        return {}

    async def get_group_info(self, group_id: str) -> dict[str, Any]:
        """Fetch group/chat info from the platform. Optional override."""
        return {}
