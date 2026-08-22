"""Tests for DiscordPluginConfig parsing."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nahida_bot.channels.discord.config import DiscordPluginConfig, parse_discord_config


class TestDiscordConfigDefaults:
    def test_defaults(self) -> None:
        config = DiscordPluginConfig()

        assert config.bot_token == ""
        assert config.proxy == ""
        assert config.allowed_guilds == []
        assert config.blocked_channels == []
        assert config.allowed_dm_users == []
        assert config.group_trigger_mode == "mention"
        assert config.group_context_capture is False
        assert config.reply_to_inbound is None
        assert config.message_max_length == 2000
        assert config.send_retry_attempts == 3

    def test_parse_from_raw_mapping(self) -> None:
        config = parse_discord_config(
            {
                "bot_token": "tok",
                "group_trigger_mode": "command",
                "allowed_guilds": ["111", 222],
            }
        )

        assert config.bot_token == "tok"
        assert config.group_trigger_mode == "command"
        assert config.allowed_guilds == ["111", "222"]

    def test_parse_none_returns_defaults(self) -> None:
        config = parse_discord_config(None)

        assert config.group_trigger_mode == "mention"


class TestDiscordConfigValidation:
    def test_id_lists_coerce_scalars(self) -> None:
        config = DiscordPluginConfig(
            allowed_guilds="777",
            blocked_channels=555,
            allowed_dm_users=None,
        )

        assert config.allowed_guilds == ["777"]
        assert config.blocked_channels == ["555"]
        assert config.allowed_dm_users == []

    def test_id_lists_reject_invalid(self) -> None:
        with pytest.raises(ValidationError):
            DiscordPluginConfig(allowed_guilds={"a": 1})

    def test_invalid_trigger_mode_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DiscordPluginConfig(group_trigger_mode="sometimes")

    def test_message_max_length_bounds(self) -> None:
        with pytest.raises(ValidationError):
            DiscordPluginConfig(message_max_length=100)
        with pytest.raises(ValidationError):
            DiscordPluginConfig(message_max_length=4000)

    def test_strings_stripped(self) -> None:
        config = DiscordPluginConfig(bot_token=" tok ", proxy=" http://p ")

        assert config.bot_token == "tok"
        assert config.proxy == "http://p"
