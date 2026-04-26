"""TelegramChannelPlugin — Telegram Bot via aiogram long polling."""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any

import structlog

from nahida_bot.channels.telegram.message_converter import TelegramMessageConverter
from nahida_bot.core.events import MessagePayload, MessageReceived
from nahida_bot.core.router import MessageRouter
from nahida_bot.plugins.base import OutboundMessage
from nahida_bot.plugins.channel_plugin import ChannelPlugin

if TYPE_CHECKING:
    from aiogram import Bot
    from nahida_bot.plugins.base import BotAPI as BotAPIProtocol
    from nahida_bot.plugins.manifest import PluginManifest

logger = structlog.get_logger(__name__)


class TelegramChannelPlugin(ChannelPlugin):
    """Telegram Bot channel using aiogram v3 long polling.

    Uses ``Bot.get_updates()`` for polling and ``Bot.send_message()`` for
    replies. Does **not** use aiogram's Dispatcher — nahida-bot's own
    MessageRouter handles command/agent dispatch.
    """

    SUPPORT_HTTP_CLIENT = True  # Bot makes outbound HTTP to Telegram API

    def __init__(self, api: BotAPIProtocol, manifest: PluginManifest) -> None:
        super().__init__(api, manifest)
        self._bot: Bot | None = None
        self._polling_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._converter = TelegramMessageConverter(bot_username=None)
        self._update_offset = 0

    async def on_load(self) -> None:
        """Create the aiogram Bot instance and verify the token."""
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties

        token = self.manifest.config.get("bot_token", "")
        if not token:
            token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not token:
            raise RuntimeError(
                "Telegram bot_token not configured. "
                "Set TELEGRAM_BOT_TOKEN env var or configure telegram.bot_token "
                "in config.yaml"
            )

        self._bot = Bot(
            token=token,
            default=DefaultBotProperties(parse_mode="HTML"),
        )
        me = await self._bot.get_me()
        self._converter = TelegramMessageConverter(bot_username=me.username)
        logger.info(
            "telegram.connected",
            bot_username=me.username,
            bot_id=me.id,
        )

    async def on_enable(self) -> None:
        """Start long polling in a background task."""
        assert self._bot is not None, "Bot not initialized — on_load failed?"
        self._polling_task = asyncio.create_task(self._poll_loop())
        logger.info("telegram.polling_started")

    async def on_disable(self) -> None:
        """Stop polling and close the bot session."""
        if self._polling_task is not None:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
            self._polling_task = None
        if self._bot is not None:
            await self._bot.session.close()
        logger.info("telegram.stopped")

    async def handle_inbound_event(self, event: dict[str, Any]) -> None:
        """Convert a Telegram Update to InboundMessage and publish.

        Text and captioned messages are passed through directly. Unsupported
        Telegram message kinds are degraded to a short textual placeholder so
        the normal router/agent path still receives the event.
        """
        message_data = event.get("message")
        if not message_data or not isinstance(message_data, dict):
            return

        normalized_message = dict(message_data)
        text = self._extract_message_text(normalized_message)
        if not text:
            return
        normalized_message["text"] = text

        inbound = self._converter.to_inbound(normalized_message)
        session_id = MessageRouter.make_session_id(inbound.platform, inbound.chat_id)

        await self.api.publish_event(
            MessageReceived(
                payload=MessagePayload(message=inbound, session_id=session_id),
                source="telegram",
            )
        )

    async def send_message(self, target: str, message: OutboundMessage) -> str:
        """Send a message via the Telegram Bot API."""
        assert self._bot is not None
        kwargs: dict[str, Any] = {
            "chat_id": int(target),
            "text": message.text,
        }
        if message.reply_to:
            try:
                kwargs["reply_to_message_id"] = int(message.reply_to)
            except ValueError:
                pass

        max_attempts = int(self.manifest.config.get("send_retry_attempts", 3))
        attempt = 0
        while True:
            attempt += 1
            try:
                sent = await self._bot.send_message(**kwargs)
                return str(sent.message_id)
            except Exception as exc:
                retry_after = self._retry_after_seconds(exc)
                if retry_after is None or attempt >= max_attempts:
                    raise
                logger.warning(
                    "telegram.send_rate_limited",
                    retry_after=retry_after,
                    attempt=attempt,
                    max_attempts=max_attempts,
                )
                await asyncio.sleep(retry_after)

    async def get_user_info(self, user_id: str) -> dict[str, Any]:
        """Fetch Telegram user profile."""
        if self._bot is None:
            return {}
        try:
            chat = await self._bot.get_chat(int(user_id))
            return {
                "id": str(chat.id),
                "username": chat.username,
                "first_name": chat.first_name,
                "last_name": chat.last_name,
            }
        except Exception:  # noqa: BLE001
            return {}

    async def get_group_info(self, group_id: str) -> dict[str, Any]:
        """Fetch Telegram chat/group info."""
        if self._bot is None:
            return {}
        try:
            chat = await self._bot.get_chat(int(group_id))
            return {
                "id": str(chat.id),
                "type": chat.type,
                "title": getattr(chat, "title", None),
            }
        except Exception:  # noqa: BLE001
            return {}

    # ── Polling Loop ─────────────────────────────────────

    async def _poll_loop(self) -> None:
        """Background task that calls ``getUpdates`` in a loop."""
        assert self._bot is not None

        polling_timeout = self.manifest.config.get("polling_timeout", 30)
        allowed_chats: list[str] = self.manifest.config.get("allowed_chats", [])
        error_backoff = 1.0
        max_error_backoff = float(self.manifest.config.get("polling_max_backoff", 30))

        while True:
            try:
                updates = await self._bot.get_updates(
                    offset=self._update_offset,
                    timeout=polling_timeout,
                )
                for update in updates:
                    self._update_offset = update.update_id + 1

                    update_dict = update.model_dump(mode="python")

                    # Filter by allowed chats if configured
                    if allowed_chats:
                        msg = update.message
                        if msg is not None:
                            chat_id = str(msg.chat.id)
                            if chat_id not in allowed_chats:
                                continue

                    await self.handle_inbound_event(update_dict)
                error_backoff = 1.0

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                retry_after = self._retry_after_seconds(exc)
                delay = retry_after if retry_after is not None else error_backoff
                logger.exception(
                    "telegram.poll_error",
                    error=str(exc),
                    retry_after=retry_after,
                    backoff_seconds=delay,
                )
                await asyncio.sleep(delay)
                if retry_after is None:
                    error_backoff = min(error_backoff * 2, max_error_backoff)

    def _extract_message_text(self, message_data: dict[str, Any]) -> str:
        """Return message text, caption, or a fallback for known non-text types."""
        text = message_data.get("text")
        if isinstance(text, str) and text:
            return text

        caption = message_data.get("caption")
        if isinstance(caption, str) and caption:
            return caption

        fallback_by_key = {
            "sticker": "[Telegram sticker]",
            "photo": "[Telegram photo]",
            "video": "[Telegram video]",
            "voice": "[Telegram voice message]",
            "audio": "[Telegram audio]",
            "document": "[Telegram document]",
            "animation": "[Telegram animation]",
            "location": "[Telegram location]",
            "contact": "[Telegram contact]",
            "poll": "[Telegram poll]",
        }
        for key, fallback in fallback_by_key.items():
            if key in message_data:
                return fallback
        return ""

    @staticmethod
    def _retry_after_seconds(exc: Exception) -> float | None:
        """Extract Telegram Retry-After seconds from aiogram/http exceptions."""
        value = getattr(exc, "retry_after", None)
        if value is None:
            parameters = getattr(exc, "parameters", None)
            value = getattr(parameters, "retry_after", None)
        if value is None:
            return None
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return None
