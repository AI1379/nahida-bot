"""Tests for TelegramChannelPlugin."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nahida_bot.channels.telegram.plugin import TelegramChannelPlugin
from nahida_bot.plugins.manifest import PluginManifest


def _make_manifest(**overrides: object) -> PluginManifest:
    defaults = {
        "id": "telegram",
        "name": "Telegram Channel",
        "version": "0.1.0",
        "entrypoint": "nahida_bot.channels.telegram.plugin:TelegramChannelPlugin",
        "type": "channel",
        "config": {"bot_token": "test-token-123"},
    }
    defaults.update(overrides)  # type: ignore[typeddict-item]
    return PluginManifest(**defaults)


class _MockAPI:
    """Minimal BotAPI mock with publish_event."""

    def __init__(self) -> None:
        self.published_events: list[Any] = []

    async def publish_event(self, event: Any) -> None:
        self.published_events.append(event)


class TestTelegramChannelPluginLifecycle:
    async def test_on_load_creates_bot(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest()
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)

        mock_bot = AsyncMock()
        mock_me = MagicMock()
        mock_me.username = "testbot"
        mock_me.id = 12345
        mock_bot.get_me.return_value = mock_me

        with patch("aiogram.Bot", return_value=mock_bot):
            await plugin.on_load()

        assert plugin._bot is mock_bot

    async def test_on_load_raises_without_token(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest(config={"bot_token": ""})
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            with pytest.raises(RuntimeError, match="bot_token not configured"):
                await plugin.on_load()

    async def test_on_enable_starts_polling(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest()
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)
        plugin._bot = AsyncMock()  # Pretend on_load succeeded

        await plugin.on_enable()
        assert plugin._polling_task is not None

        # Cleanup
        await plugin.on_disable()

    async def test_on_disable_stops_polling(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest()
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)
        plugin._bot = AsyncMock()

        await plugin.on_enable()
        assert plugin._polling_task is not None

        await plugin.on_disable()
        assert plugin._polling_task is None


class TestTelegramChannelPluginMessaging:
    async def test_send_message_calls_bot_api(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest()
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)

        mock_bot = AsyncMock()
        mock_sent = MagicMock()
        mock_sent.message_id = 42
        mock_bot.send_message.return_value = mock_sent
        plugin._bot = mock_bot

        from nahida_bot.plugins.base import OutboundMessage

        result = await plugin.send_message("123", OutboundMessage(text="hi"))

        mock_bot.send_message.assert_awaited_once()
        call_kwargs = mock_bot.send_message.call_args[1]
        assert call_kwargs["chat_id"] == 123
        assert call_kwargs["text"] == "hi"
        assert result == "42"

    async def test_send_message_with_reply(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest()
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)

        mock_bot = AsyncMock()
        mock_sent = MagicMock()
        mock_sent.message_id = 43
        mock_bot.send_message.return_value = mock_sent
        plugin._bot = mock_bot

        from nahida_bot.plugins.base import OutboundMessage

        await plugin.send_message("123", OutboundMessage(text="reply", reply_to="10"))

        call_kwargs = mock_bot.send_message.call_args[1]
        assert call_kwargs["reply_to_message_id"] == 10

    async def test_handle_inbound_publishes_event(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest()
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)

        update = {
            "message": {
                "message_id": 1,
                "date": 1700000000,
                "chat": {"id": 100, "type": "private"},
                "from_user": {"id": 200, "first_name": "User"},
                "text": "/help",
            }
        }

        await plugin.handle_inbound_event(update)

        assert len(api.published_events) == 1
        event = api.published_events[0]
        inbound = event.payload.message
        assert inbound.text == "/help"
        assert inbound.platform == "telegram"
        assert inbound.chat_id == "100"

    async def test_handle_inbound_ignores_non_text(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest()
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)

        # Sticker message (no text)
        update = {
            "message": {
                "message_id": 2,
                "date": 1700000000,
                "chat": {"id": 100, "type": "private"},
                "from_user": {"id": 200},
                "sticker": {"file_id": "abc"},
            }
        }

        await plugin.handle_inbound_event(update)
        assert len(api.published_events) == 0

    async def test_handle_inbound_ignores_empty_message(self) -> None:
        api = _MockAPI()
        manifest = _make_manifest()
        plugin = TelegramChannelPlugin(api=api, manifest=manifest)

        # No message field at all (e.g. callback_query)
        update = {"callback_query": {"id": "1", "data": "click"}}

        await plugin.handle_inbound_event(update)
        assert len(api.published_events) == 0


class TestTelegramChannelPluginCommunicationFlags:
    def test_supports_http_client(self) -> None:
        assert TelegramChannelPlugin.SUPPORT_HTTP_CLIENT is True
        assert TelegramChannelPlugin.SUPPORT_HTTP_SERVER is False
        assert TelegramChannelPlugin.SUPPORT_WEBSOCKET_CLIENT is False
        assert TelegramChannelPlugin.SUPPORT_WEBSOCKET_SERVER is False
        assert TelegramChannelPlugin.SUPPORT_SSE is False
