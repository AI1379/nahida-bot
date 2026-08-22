"""Discord transport — the only module that touches discord.py.

Wraps a ``discord.Client`` gateway connection and exposes a small
dict-in/dict-out surface so the plugin and its tests never import
discord.py directly. Following the telegram/aiogram precedent: the
library owns protocol plumbing (gateway heartbeat/resume, rate limits,
REST), nahida-bot owns event semantics.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

import structlog

import discord


logger = structlog.get_logger(__name__)


EventCallback = Callable[[dict[str, Any]], Awaitable[None]]


def _channel_type_name(channel: Any) -> str:
    name = getattr(getattr(channel, "type", None), "name", "") or "unknown"
    if name == "private":
        return "dm"
    if name == "group":
        return "group_dm"
    return name


def message_to_event(message: Any) -> dict[str, Any]:
    """Convert a ``discord.Message`` into a plain dict event payload."""
    channel = message.channel
    guild_id = str(message.guild.id) if message.guild else ""
    author = message.author
    mentions = [
        {"id": str(user.id), "name": user.display_name}
        for user in getattr(message, "mentions", ())
    ]
    attachments = [
        {
            "id": str(attachment.id),
            "filename": attachment.filename,
            "content_type": attachment.content_type or "",
            "size": attachment.size,
            "url": str(attachment.url),
            "width": attachment.width or 0,
            "height": attachment.height or 0,
            "spoiler": attachment.is_spoiler(),
        }
        for attachment in message.attachments
    ]
    reference = getattr(message, "reference", None)
    reference_message_id = ""
    if reference is not None:
        reference_message_id = str(reference.message_id or "")

    thread = getattr(channel, "parent", None)
    return {
        "kind": "message",
        "message": {
            "id": str(message.id),
            "type": getattr(getattr(message, "type", None), "name", "default"),
            "content": message.content or "",
            "timestamp": message.created_at.timestamp(),
            "author": {
                "id": str(author.id),
                "name": str(getattr(author, "name", "") or ""),
                "display_name": str(getattr(author, "display_name", "") or ""),
                "bot": bool(getattr(author, "bot", False)),
            },
            "guild_id": guild_id,
            "channel": {
                "id": str(channel.id),
                "type": _channel_type_name(channel),
                "name": str(getattr(channel, "name", "") or ""),
                "guild_id": guild_id,
                "parent_id": str(thread.id) if thread is not None else "",
            },
            "mentions": mentions,
            "mention_everyone": bool(message.mention_everyone),
            "attachments": attachments,
            "embed_count": len(getattr(message, "embeds", ()) or ()),
            "reference_message_id": reference_message_id,
        },
    }


class _Client(discord.Client):
    """Gateway client that forwards every message as a dict event."""

    def __init__(
        self, *, intents: discord.Intents, proxy: str, on_event: EventCallback
    ) -> None:
        super().__init__(intents=intents, proxy=proxy or None)
        self._on_event = on_event

    async def on_ready(self) -> None:
        user = self.user
        logger.info(
            "discord.gateway_ready",
            bot_username=getattr(user, "name", ""),
            bot_id=getattr(user, "id", ""),
            guilds=len(self.guilds),
        )

    async def on_message(self, message: discord.Message) -> None:
        if self.user is not None and message.author.id == self.user.id:
            return
        try:
            event = message_to_event(message)
        except Exception:  # noqa: BLE001 - a malformed event must not kill the loop
            logger.exception("discord.event_convert_failed", message_id=str(message.id))
            return
        try:
            await self._on_event(event)
        except Exception:  # noqa: BLE001
            logger.exception("discord.event_handler_failed", message_id=str(message.id))


class DiscordTransport:
    """Small facade over the discord.py client used by the plugin.

    Lifecycle mirrors the telegram channel: ``login()`` verifies the token
    and yields the bot identity at plugin-load time (fail fast), ``start()``
    opens the gateway connection in the background, ``close()`` shuts down.
    """

    def __init__(self, *, token: str, proxy: str = "", on_event: EventCallback) -> None:
        intents = discord.Intents.default()
        # Privileged: must also be enabled in the Discord developer portal.
        intents.message_content = True
        self._client = _Client(intents=intents, proxy=proxy, on_event=on_event)
        self._token = token
        self.bot_user: dict[str, str] = {}

    async def login(self) -> dict[str, str]:
        """Verify the token and return the bot's own user identity."""
        await self._client.login(self._token)
        user = self._client.user
        if user is None:  # pragma: no cover - login() sets user or raises
            raise RuntimeError("Discord login succeeded without a user")
        self.bot_user = {
            "id": str(user.id),
            "username": str(getattr(user, "name", "") or ""),
        }
        return dict(self.bot_user)

    async def start(self) -> None:
        """Connect the gateway (runs until close()). Requires login() first."""
        await self._client.connect()

    async def close(self) -> None:
        await self._client.close()

    # ── Outbound ──────────────────────────────────────────

    async def _resolve_channel(self, target: str) -> Any:
        channel = self._client.get_channel(int(target))
        if channel is None:
            channel = await self._client.fetch_channel(int(target))
        return channel

    async def send_text(self, target: str, text: str, reply_to: str = "") -> str:
        channel = await self._resolve_channel(target)
        kwargs: dict[str, Any] = {"content": text}
        if reply_to:
            kwargs["reference"] = discord.MessageReference(
                message_id=int(reply_to), channel_id=channel.id
            )
        message = await channel.send(**kwargs)
        return str(message.id)

    async def send_file(
        self, target: str, path: str, filename: str = "", caption: str = ""
    ) -> str:
        channel = await self._resolve_channel(target)
        file = discord.File(Path(path), filename=filename or None)
        kwargs: dict[str, Any] = {"file": file}
        if caption:
            kwargs["content"] = caption
        message = await channel.send(**kwargs)
        return str(message.id)

    # ── Info lookups ──────────────────────────────────────

    async def fetch_user_info(self, user_id: str) -> dict[str, Any]:
        user = await self._client.fetch_user(int(user_id))
        return {
            "id": str(user.id),
            "username": str(getattr(user, "name", "") or ""),
            "display_name": str(getattr(user, "display_name", "") or ""),
            "bot": bool(getattr(user, "bot", False)),
        }

    async def fetch_channel_info(self, channel_id: str) -> dict[str, Any]:
        channel = await self._client.fetch_channel(int(channel_id))
        guild = getattr(channel, "guild", None)
        return {
            "id": str(channel.id),
            "name": str(getattr(channel, "name", "") or ""),
            "type": _channel_type_name(channel),
            "guild_id": str(guild.id) if guild is not None else "",
        }
