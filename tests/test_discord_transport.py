"""Tests for the discord.py touchpoint layer (DiscordTransport).

Only the outbound text path is exercised — with a stubbed channel so no
gateway or REST call is ever made. These tests exist to cover conversion
details the FakeTransport in test_discord_plugin.py cannot (it records
arguments instead of building real discord.py objects).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from nahida_bot.channels.discord.transport import DiscordTransport, parse_snowflake


def _transport_with_stub_channel() -> tuple[DiscordTransport, AsyncMock]:
    """Build a transport whose channel resolution returns a stub channel."""
    transport = object.__new__(DiscordTransport)

    async def _resolve_channel(target: str) -> SimpleNamespace:
        return channel

    channel = SimpleNamespace(
        id=111,
        send=AsyncMock(return_value=SimpleNamespace(id=222)),
    )
    transport._resolve_channel = _resolve_channel  # type: ignore[method-assign]
    return transport, channel.send


class TestParseSnowflake:
    def test_numeric_id(self) -> None:
        assert parse_snowflake("1001") == 1001

    def test_synthetic_interaction_id_is_none(self) -> None:
        assert parse_snowflake("interaction-5001") is None

    def test_empty_is_none(self) -> None:
        assert parse_snowflake("") is None


class TestSendTextReplyReference:
    async def test_synthetic_reply_id_drops_reference_instead_of_raising(self) -> None:
        transport, send = _transport_with_stub_channel()

        # Regression: int("interaction-5001") used to raise ValueError and
        # lose the whole reply to a slash command.
        sent_id = await transport.send_text("111", "hi", reply_to="interaction-5001")

        send.assert_awaited_once_with(content="hi")
        assert sent_id == "222"

    async def test_numeric_reply_id_builds_reference(self) -> None:
        transport, send = _transport_with_stub_channel()

        await transport.send_text("111", "hi", reply_to="1001")

        reference = send.await_args.kwargs["reference"]
        assert reference.message_id == 1001
        assert reference.channel_id == 111

    async def test_no_reply_id_builds_no_reference(self) -> None:
        transport, send = _transport_with_stub_channel()

        await transport.send_text("111", "hi")

        send.assert_awaited_once_with(content="hi")
