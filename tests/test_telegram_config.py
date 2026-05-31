"""Tests for Telegram channel configuration."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nahida_bot.channels.telegram.config import (
    TelegramPluginConfig,
    parse_telegram_config,
)


def test_telegram_config_defaults() -> None:
    config = TelegramPluginConfig()

    assert config.bot_token == ""
    assert config.proxy == ""
    assert config.polling_timeout == 30
    assert config.polling_max_backoff == 30.0
    assert config.allowed_chats == []
    assert config.group_trigger_mode == "always"
    assert config.group_context_capture is False
    assert config.reply_to_inbound is None
    assert config.send_retry_attempts == 3
    assert config.media_download_dir == "./data/temp/media"


def test_telegram_config_normalizes_chat_ids_and_strings() -> None:
    config = parse_telegram_config(
        {
            "bot_token": " token ",
            "proxy": " http://127.0.0.1:8080 ",
            "allowed_chats": [123, "-456"],
            "media_download_dir": " ./media ",
        }
    )

    assert config.bot_token == "token"
    assert config.proxy == "http://127.0.0.1:8080"
    assert config.allowed_chats == ["123", "-456"]
    assert config.media_download_dir == "./media"


def test_telegram_config_accepts_single_chat_id() -> None:
    config = parse_telegram_config({"allowed_chats": -100123})

    assert config.allowed_chats == ["-100123"]


def test_telegram_config_rejects_invalid_group_trigger_mode() -> None:
    with pytest.raises(ValidationError):
        parse_telegram_config({"group_trigger_mode": "all"})


def test_telegram_config_rejects_invalid_retry_count() -> None:
    with pytest.raises(ValidationError):
        parse_telegram_config({"send_retry_attempts": 0})
