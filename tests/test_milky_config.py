"""Tests for Milky channel configuration."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nahida_bot.channels.milky.config import MilkyPluginConfig, parse_milky_config


def test_milky_config_defaults() -> None:
    config = MilkyPluginConfig()

    assert config.normalized_base_url == "http://127.0.0.1:3000"
    assert config.api_base_url == "http://127.0.0.1:3000/api"
    assert config.event_ws_url == "ws://127.0.0.1:3000/event"
    assert config.command_prefix == "/"
    assert config.group_trigger_mode == "mention"
    assert config.reply_to_inbound is None
    assert config.allowed_friends == []
    assert config.allowed_groups == []
    assert config.cache_media_on_receive is True
    assert config.max_forward_depth == 3
    assert config.max_forward_messages == 80
    assert config.scene_cache_size == 4096


def test_milky_config_normalizes_paths_and_ids() -> None:
    config = parse_milky_config(
        {
            "base_url": "https://example.com:8443/root/",
            "api_prefix": "api",
            "event_path": "event",
            "allowed_friends": 12345,
            "allowed_groups": [1000, "2000"],
        }
    )

    assert config.api_prefix == "/api"
    assert config.event_path == "/event"
    assert config.api_base_url == "https://example.com:8443/root/api"
    assert config.event_ws_url == "wss://example.com:8443/root/event"
    assert config.allowed_friends == ["12345"]
    assert config.allowed_groups == ["1000", "2000"]


def test_milky_config_accepts_reply_to_inbound_override() -> None:
    config = parse_milky_config({"reply_to_inbound": False})

    assert config.reply_to_inbound is False


def test_milky_config_accepts_ws_url_override() -> None:
    config = parse_milky_config(
        {
            "base_url": "http://127.0.0.1:3000",
            "ws_url": "ws://127.0.0.1:3000/custom-event",
        }
    )

    assert config.event_ws_url == "ws://127.0.0.1:3000/custom-event"


def test_milky_config_rejects_invalid_group_trigger_mode() -> None:
    with pytest.raises(ValidationError):
        parse_milky_config({"group_trigger_mode": "all"})


def test_milky_config_rejects_invalid_reconnect_delay_order() -> None:
    with pytest.raises(ValidationError):
        parse_milky_config(
            {
                "reconnect_initial_delay": 10.0,
                "reconnect_max_delay": 1.0,
            }
        )
