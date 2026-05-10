"""TelegramPlugin — Telegram Bot via aiogram long polling."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from nahida_bot.channels.telegram.markdown_converter import (
    convert_markdown_to_telegram_html,
    split_html_message,
)
from nahida_bot.channels.telegram.message_converter import TelegramMessageConverter
from nahida_bot.core.events import MessagePayload, MessageReceived
from nahida_bot.core.router import MessageRouter
from nahida_bot.plugins.base import (
    Attachment,
    MediaDownloadResult,
    OutboundMessage,
    Plugin,
)

if TYPE_CHECKING:
    from aiogram import Bot
    from nahida_bot.plugins.base import BotAPI as BotAPIProtocol
    from nahida_bot.plugins.manifest import PluginManifest

logger = structlog.get_logger(__name__)


class TelegramPlugin(Plugin):
    """Telegram Bot channel using aiogram v3 long polling.

    Uses ``Bot.get_updates()`` for polling and ``Bot.send_message()`` for
    replies. Does **not** use aiogram's Dispatcher — nahida-bot's own
    MessageRouter handles command/agent dispatch.
    """

    def __init__(self, api: BotAPIProtocol, manifest: PluginManifest) -> None:
        super().__init__(api, manifest)
        self._channel_id = manifest.id
        self._bot: Bot | None = None
        self._polling_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._converter = TelegramMessageConverter(bot_username=None)
        self._update_offset = 0

    @property
    def channel_id(self) -> str:
        """Unique identifier used by the channel registry."""
        return self._channel_id

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

        proxy = os.environ.get("TELEGRAM_PROXY") or self.manifest.config.get("proxy")

        bot_kwargs: dict[str, Any] = {
            "token": token,
            "default": DefaultBotProperties(parse_mode="HTML"),
        }
        if proxy:
            from aiogram.client.session.aiohttp import AiohttpSession

            bot_kwargs["session"] = AiohttpSession(proxy=proxy)
            logger.info("telegram.proxy_configured", proxy=proxy)

        bot = Bot(**bot_kwargs)
        self._bot = bot
        me = await bot.get_me()
        self._converter = TelegramMessageConverter(bot_username=me.username)
        logger.info(
            "telegram.connected",
            bot_username=me.username,
            bot_id=me.id,
        )
        self.api.register_channel(self)

    async def on_enable(self) -> None:
        """Start long polling and register the download_media tool."""
        assert self._bot is not None, "Bot not initialized — on_load failed?"
        self._polling_task = asyncio.create_task(self._poll_loop())
        self._register_download_tool()
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

        Text, captioned media, and non-text media messages are all handled.
        Media metadata (file_id, dimensions, etc.) is embedded in the text so
        the agent can reason about attached files.
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
        """Send a message via the Telegram Bot API.

        Converts Markdown to Telegram HTML, splits long messages, and
        handles photo/document attachments.
        """
        assert self._bot is not None
        last_msg_id = ""

        # 1. Send text (converted from Markdown to Telegram HTML)
        if message.text:
            html_text = convert_markdown_to_telegram_html(message.text)
            chunks = split_html_message(html_text)
            for i, chunk in enumerate(chunks):
                kwargs: dict[str, Any] = {
                    "chat_id": int(target),
                    "text": chunk,
                }
                if message.reply_to and i == 0:
                    try:
                        kwargs["reply_to_message_id"] = int(message.reply_to)
                    except ValueError:
                        pass

                sent = await self._send_with_retry(self._bot.send_message, kwargs)
                last_msg_id = str(sent.message_id)

        # 2. Send attachments
        for attachment in message.attachments:
            sent_id = await self._send_attachment(target, attachment)
            if sent_id:
                last_msg_id = sent_id

        return last_msg_id

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

    # ── Inbound Media ────────────────────────────────────

    def _extract_message_text(self, message_data: dict[str, Any]) -> str:
        """Return message text, caption, or structured media info."""
        text = message_data.get("text")
        if isinstance(text, str) and text:
            return text

        parts: list[str] = []

        caption = message_data.get("caption")
        if isinstance(caption, str) and caption:
            parts.append(caption)

        media_info = self._extract_media_info(message_data)
        if media_info:
            parts.append(
                f"[Media: type={media_info['type']}, file_id={media_info['file_id']}]"
            )

        return " ".join(parts) if parts else ""

    def _extract_media_info(
        self, message_data: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Extract file metadata from known Telegram media types."""
        # Photo: list of PhotoSize objects serialised as dicts
        photos = message_data.get("photo")
        if isinstance(photos, list) and photos:
            largest = photos[-1]
            return {
                "type": "photo",
                "file_id": largest.get("file_id", ""),
                "width": largest.get("width", 0),
                "height": largest.get("height", 0),
            }

        doc = message_data.get("document")
        if isinstance(doc, dict):
            return {
                "type": "document",
                "file_id": doc.get("file_id", ""),
                "file_name": doc.get("file_name", ""),
                "mime_type": doc.get("mime_type", ""),
                "file_size": doc.get("file_size", 0),
            }

        video = message_data.get("video")
        if isinstance(video, dict):
            return {
                "type": "video",
                "file_id": video.get("file_id", ""),
                "mime_type": video.get("mime_type", ""),
                "file_size": video.get("file_size", 0),
            }

        audio = message_data.get("audio")
        if isinstance(audio, dict):
            return {
                "type": "audio",
                "file_id": audio.get("file_id", ""),
                "file_name": audio.get("file_name", ""),
                "mime_type": audio.get("mime_type", ""),
            }

        voice = message_data.get("voice")
        if isinstance(voice, dict):
            return {
                "type": "voice",
                "file_id": voice.get("file_id", ""),
                "duration": voice.get("duration", 0),
            }

        sticker = message_data.get("sticker")
        if isinstance(sticker, dict):
            emoji = sticker.get("emoji", "")
            return {
                "type": "sticker",
                "file_id": sticker.get("file_id", ""),
                "emoji": emoji,
            }

        animation = message_data.get("animation")
        if isinstance(animation, dict):
            return {
                "type": "animation",
                "file_id": animation.get("file_id", ""),
                "mime_type": animation.get("mime_type", ""),
            }

        # Fallback for remaining non-text types
        for key in ("location", "contact", "poll"):
            if key in message_data:
                return {"type": key, "file_id": ""}

        return None

    # ── Outbound Helpers ─────────────────────────────────

    async def _send_with_retry(
        self, send_func: Any, kwargs: dict[str, Any], *, max_attempts: int | None = None
    ) -> Any:
        """Call a Telegram API method with rate-limit retry logic."""
        if max_attempts is None:
            max_attempts = int(self.manifest.config.get("send_retry_attempts", 3))

        attempt = 0
        while True:
            attempt += 1
            try:
                return await send_func(**kwargs)
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

    async def _send_attachment(self, target: str, attachment: Attachment) -> str | None:
        """Send a single attachment via the Telegram Bot API."""
        if self._bot is None:
            return None

        from aiogram.types import FSInputFile

        file_path = Path(attachment.path)
        if not file_path.is_file():
            logger.warning("telegram.attachment_missing", path=attachment.path)
            return None

        input_file = FSInputFile(
            str(file_path), filename=attachment.filename or file_path.name
        )

        try:
            if attachment.type == "photo":
                sent = await self._send_with_retry(
                    self._bot.send_photo,
                    {
                        "chat_id": int(target),
                        "photo": input_file,
                        "caption": attachment.caption or None,
                    },
                )
            else:
                sent = await self._send_with_retry(
                    self._bot.send_document,
                    {
                        "chat_id": int(target),
                        "document": input_file,
                        "caption": attachment.caption or None,
                    },
                )
            return str(sent.message_id)
        except Exception:  # noqa: BLE001
            logger.exception("telegram.attachment_send_failed", path=attachment.path)
            return None

    # ── Media Download ───────────────────────────────────

    async def download_media(
        self, file_id: str, destination: str | None = None
    ) -> MediaDownloadResult | None:
        """Download a file from Telegram by file_id."""
        if self._bot is None:
            return None

        try:
            file = await self._bot.get_file(file_id)
        except Exception:  # noqa: BLE001
            logger.exception("telegram.get_file_failed", file_id=file_id)
            return None

        if not file.file_path:
            return None

        media_dir = self.manifest.config.get("media_download_dir", "./data/temp/media")
        dest = Path(destination) if destination else Path(media_dir) / f"{file_id}.dat"
        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            await self._bot.download_file(file.file_path, destination=str(dest))
        except Exception:  # noqa: BLE001
            logger.exception("telegram.download_failed", file_id=file_id)
            return None

        return MediaDownloadResult(
            path=str(dest),
            file_name=dest.name,
            file_size=dest.stat().st_size,
        )

    def _register_download_tool(self) -> None:
        """Register the download_media tool so the agent can fetch files."""
        import json

        async def _handler(*, file_id: str, file_name: str = "") -> str:
            dest = None
            if file_name:
                media_dir = self.manifest.config.get(
                    "media_download_dir", "./data/temp/media"
                )
                dest = str(Path(media_dir) / file_name)

            result = await self.download_media(file_id, destination=dest)
            if result is None:
                return json.dumps({"error": f"Failed to download file {file_id}"})
            return json.dumps(
                {
                    "path": result.path,
                    "file_name": result.file_name,
                    "file_size": result.file_size,
                }
            )

        self.api.register_tool(
            "download_media",
            "Download a media file from Telegram by file_id. "
            "Use this when the user sends a photo, document, or other file and you "
            "need to access its contents. The file_id is included in the message "
            "as [Media: type=..., file_id=...]. Returns the local file path.",
            {
                "type": "object",
                "properties": {
                    "file_id": {
                        "type": "string",
                        "description": "The Telegram file_id to download.",
                    },
                    "file_name": {
                        "type": "string",
                        "description": "Optional filename for the downloaded file.",
                    },
                },
                "required": ["file_id"],
                "additionalProperties": False,
            },
            _handler,
        )

    # ── Utility ──────────────────────────────────────────

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
