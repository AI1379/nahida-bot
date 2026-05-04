"""Tests for Milky inbound message conversion."""

from __future__ import annotations

import pytest

from nahida_bot.channels.milky.config import parse_milky_config
from nahida_bot.channels.milky.message_converter import MilkyMessageConverter
from nahida_bot.channels.milky.segments import (
    IncomingForwardedMessage,
    IncomingTextSegment,
)

pytestmark = pytest.mark.asyncio


def _message(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "message_scene": "friend",
        "peer_id": 10001,
        "sender_id": 10001,
        "message_seq": 123,
        "time": 1700000000,
        "segments": [{"type": "text", "data": {"text": "hello"}}],
    }
    data.update(overrides)
    return data


async def test_friend_message_to_inbound() -> None:
    converter = MilkyMessageConverter(parse_milky_config({}))

    inbound = await converter.to_inbound(_message())

    assert inbound is not None
    assert inbound.platform == "milky"
    assert inbound.chat_id == "10001"
    assert inbound.user_id == "10001"
    assert inbound.message_id == "123"
    assert inbound.text == "hello"
    assert inbound.is_group is False
    assert inbound.command_prefix == "/"


async def test_group_mention_strips_self_mention() -> None:
    converter = MilkyMessageConverter(
        parse_milky_config({"group_trigger_mode": "mention"}),
        self_id=999,
    )

    inbound = await converter.to_inbound(
        _message(
            message_scene="group",
            peer_id=20001,
            sender_id=10001,
            segments=[
                {"type": "mention", "data": {"user_id": 999, "name": "bot"}},
                {"type": "text", "data": {"text": " help"}},
            ],
        )
    )

    assert inbound is not None
    assert inbound.is_group is True
    assert inbound.chat_id == "20001"
    assert inbound.text == "help"


async def test_group_message_without_trigger_is_ignored() -> None:
    converter = MilkyMessageConverter(
        parse_milky_config({"group_trigger_mode": "mention"}),
        self_id=999,
    )

    inbound = await converter.to_inbound(_message(message_scene="group", peer_id=20001))

    assert inbound is None


async def test_group_command_trigger_is_accepted() -> None:
    converter = MilkyMessageConverter(
        parse_milky_config({"group_trigger_mode": "command", "command_prefix": "!"}),
        self_id=999,
    )

    inbound = await converter.to_inbound(
        _message(
            message_scene="group",
            peer_id=20001,
            segments=[{"type": "text", "data": {"text": "!help"}}],
        )
    )

    assert inbound is not None
    assert inbound.text == "!help"
    assert inbound.command_prefix == "!"


async def test_allowed_lists_filter_messages() -> None:
    converter = MilkyMessageConverter(parse_milky_config({"allowed_friends": ["42"]}))

    assert await converter.to_inbound(_message(peer_id=10001)) is None
    assert await converter.to_inbound(_message(peer_id=42)) is not None


async def test_reply_and_media_rendering() -> None:
    converter = MilkyMessageConverter(parse_milky_config({}))

    inbound = await converter.to_inbound(
        _message(
            segments=[
                {"type": "reply", "data": {"message_seq": 7}},
                {
                    "type": "image",
                    "data": {
                        "resource_id": "img-1",
                        "width": 640,
                        "height": 480,
                        "summary": "[image]",
                    },
                },
                {
                    "type": "file",
                    "data": {
                        "file_id": "file-1",
                        "file_name": "report.pdf",
                        "file_size": 1024,
                    },
                },
            ]
        )
    )

    assert inbound is not None
    assert inbound.reply_to == "7"
    assert "[Media: type=image, resource_id=img-1" in inbound.text
    assert "[File: name=report.pdf, file_id=file-1" in inbound.text


async def test_resolves_forward_messages() -> None:
    class Client:
        async def get_forwarded_messages(
            self, forward_id: str
        ) -> list[IncomingForwardedMessage]:
            assert forward_id == "forward-1"
            return [
                IncomingForwardedMessage(
                    message_seq=1,
                    sender_name="Alice",
                    segments=[IncomingTextSegment("inside")],
                )
            ]

    converter = MilkyMessageConverter(
        parse_milky_config({"max_forward_depth": 2}),
        forward_client=Client(),
    )

    inbound = await converter.to_inbound(
        _message(
            segments=[
                {
                    "type": "forward",
                    "data": {
                        "forward_id": "forward-1",
                        "title": "History",
                        "summary": "1 message",
                    },
                }
            ]
        )
    )

    assert inbound is not None
    assert "- Alice: inside" in inbound.text


async def test_forward_resolution_failure_keeps_reference_text() -> None:
    class Client:
        async def get_forwarded_messages(
            self, forward_id: str
        ) -> list[IncomingForwardedMessage]:
            raise RuntimeError("Lagrange.Milky does not implement this API yet")

    converter = MilkyMessageConverter(
        parse_milky_config({"max_forward_depth": 2}),
        forward_client=Client(),
    )

    inbound = await converter.to_inbound(
        _message(
            segments=[
                {
                    "type": "forward",
                    "data": {
                        "forward_id": "forward-1",
                        "title": "History",
                        "preview": ["Alice: hello"],
                        "summary": "1 message",
                    },
                }
            ]
        )
    )

    assert inbound is not None
    assert "[Forward: id=forward-1" in inbound.text
    assert "Alice: hello" in inbound.text
