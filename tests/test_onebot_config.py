"""Tests for OneBot channel configuration."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nahida_bot.channels.onebot.config import parse_onebot_config


def test_onebot_config_accepts_v11_forward_ws() -> None:
    config = parse_onebot_config({"ws_url": "ws://127.0.0.1:3001"})

    assert config.protocol_version == "v11"
    assert config.ws_url == "ws://127.0.0.1:3001"


def test_onebot_config_rejects_missing_ws_url() -> None:
    with pytest.raises(ValidationError, match="requires ws_url"):
        parse_onebot_config({})


def test_onebot_config_rejects_webhook_mode_until_implemented() -> None:
    with pytest.raises(ValidationError, match="WebHook mode is not yet implemented"):
        parse_onebot_config({"webhook_enabled": True})


def test_onebot_config_rejects_v12_until_implemented() -> None:
    with pytest.raises(ValidationError, match="v12 support is not yet implemented"):
        parse_onebot_config(
            {
                "protocol_version": "v12",
                "ws_url": "ws://127.0.0.1:3001",
            }
        )


def test_onebot_config_rejects_non_ws_url() -> None:
    with pytest.raises(ValidationError, match="ws_url must start"):
        parse_onebot_config({"ws_url": "http://127.0.0.1:3001"})
