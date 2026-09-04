"""Tests for the Feishu inbound message converter."""

from __future__ import annotations

import json
from typing import Any

import pytest

from nahida_bot.channels.feishu.config import FeishuPluginConfig
from nahida_bot.channels.feishu.message_converter import FeishuMessageConverter

pytestmark = pytest.mark.asyncio

BOT_OPEN_ID = "ou_bot00000000000000000000000000"


def _event(
    *,
    chat_type: str = "group",
    message_type: str = "text",
    content: dict[str, Any] | None = None,
    mentions: list[dict[str, Any]] | None = None,
    sender_open_id: str = "ou_user1111111111111111111111111",
    sender_type: str = "user",
    message_id: str = "om_1",
    chat_id: str = "oc_chat1",
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "message_id": message_id,
        "chat_id": chat_id,
        "chat_type": chat_type,
        "message_type": message_type,
        "content": json.dumps(content or {"text": "hello"}, ensure_ascii=False),
        "create_time": "1609073151345",
    }
    if mentions is not None:
        message["mentions"] = mentions
    return {
        "schema": "2.0",
        "header": {"event_type": "im.message.receive_v1", "app_id": "cli_x"},
        "event": {
            "sender": {
                "sender_id": {"open_id": sender_open_id},
                "sender_type": sender_type,
            },
            "message": message,
        },
    }


def _converter(**kwargs: Any) -> FeishuMessageConverter:
    return FeishuMessageConverter(
        FeishuPluginConfig(),
        self_open_id=kwargs.pop("self_open_id", BOT_OPEN_ID),
        **kwargs,
    )


async def test_converts_group_text_message() -> None:
    inbound = await _converter().to_inbound(_event())

    assert inbound is not None
    assert inbound.platform == "feishu"
    assert inbound.chat_id == "oc_chat1"
    assert inbound.user_id == "ou_user1111111111111111111111111"
    assert inbound.text == "hello"
    assert inbound.is_group is True
    assert inbound.timestamp == pytest.approx(1609073151.345)
    assert inbound.sender_context is not None and inbound.sender_context.is_bot is False
    assert (
        inbound.chat_context is not None and inbound.chat_context.chat_type == "group"
    )


async def test_p2p_message_marked_private() -> None:
    inbound = await _converter().to_inbound(_event(chat_type="p2p"))

    assert inbound is not None
    assert inbound.is_group is False
    assert (
        inbound.chat_context is not None and inbound.chat_context.chat_type == "private"
    )


async def test_mention_placeholder_replaced_and_bot_mention_detected() -> None:
    mentions = [
        {
            "key": "@_user_1",
            "id": {"open_id": BOT_OPEN_ID},
            "mentioned_type": "bot",
            "name": "纳西妲",
        },
        {
            "key": "@_user_2",
            "id": {"open_id": "ou_other222222222222222222222222"},
            "mentioned_type": "user",
            "name": "张三",
        },
    ]
    inbound = await _converter().to_inbound(
        _event(content={"text": "@_user_1 @_user_2 今天天气如何"}, mentions=mentions)
    )

    assert inbound is not None
    assert inbound.mentions_bot is True
    # Self-mention stripped; other mention rendered as @name.
    assert "纳西妲" not in inbound.text
    assert "@张三" in inbound.text
    assert inbound.mentioned_user_ids == (
        BOT_OPEN_ID,
        "ou_other222222222222222222222222",
    )


async def test_mentions_bot_falls_back_to_bot_type_when_self_unknown() -> None:
    mentions = [
        {
            "key": "@_user_1",
            "id": {"open_id": "ou_unknownbot111111111111111111"},
            "mentioned_type": "bot",
            "name": "other bot",
        }
    ]
    inbound = await _converter(self_open_id="").to_inbound(
        _event(content={"text": "@_user_1 hi"}, mentions=mentions)
    )

    assert inbound is not None
    assert inbound.mentions_bot is True


async def test_post_message_text_and_attachments() -> None:
    content = {
        "post": {
            "zh_cn": {
                "title": "标题",
                "content": [
                    [
                        {"tag": "text", "text": "看看这张 "},
                        {"tag": "at", "user_id": "ou_other222222222222222222222222"},
                        {"tag": "img", "image_key": "img_v2_1"},
                    ],
                    [{"tag": "a", "text": "链接", "href": "https://example.com"}],
                ],
            }
        }
    }
    mentions = [
        {
            "key": "@_user_1",
            "id": {"open_id": "ou_other222222222222222222222222"},
            "name": "李四",
        }
    ]
    inbound = await _converter().to_inbound(
        _event(message_type="post", content=content, mentions=mentions)
    )

    assert inbound is not None
    assert "看看这张" in inbound.text
    assert "@李四" in inbound.text  # at element resolved via mentions
    assert "链接(https://example.com)" in inbound.text
    assert len(inbound.attachments) == 1
    att = inbound.attachments[0]
    assert att.kind == "image"
    assert att.platform_id == "om_1:img_v2_1"
    assert att.metadata["file_key"] == "img_v2_1"


async def test_media_message_types_produce_attachments() -> None:
    cases = [
        ("image", {"image_key": "img_v2_9"}, "image", "img_v2_9"),
        (
            "file",
            {"file_key": "file_v2_9", "file_name": "报告.pdf"},
            "file",
            "file_v2_9",
        ),
        ("audio", {"file_key": "file_v2_8", "duration": 1500}, "audio", "file_v2_8"),
        (
            "media",
            {"file_key": "file_v2_7", "image_key": "img_v2_7"},
            "video",
            "file_v2_7",
        ),
    ]
    for message_type, content, kind, file_key in cases:
        inbound = await _converter().to_inbound(
            _event(message_type=message_type, content=content)
        )
        assert inbound is not None, message_type
        assert inbound.attachments[0].kind == kind, message_type
        assert inbound.attachments[0].platform_id == f"om_1:{file_key}"


async def test_sticker_becomes_placeholder_not_attachment() -> None:
    inbound = await _converter().to_inbound(
        _event(message_type="sticker", content={"sticker_key": "st_1"})
    )

    assert inbound is not None
    assert inbound.text == "[表情包]"
    assert inbound.attachments == []


async def test_reply_chain_populates_reply_to() -> None:
    event = _event()
    event["event"]["message"]["parent_id"] = "om_parent"
    event["event"]["message"]["root_id"] = "om_root"

    inbound = await _converter().to_inbound(event)

    assert inbound is not None
    assert inbound.reply_to == "om_parent"


async def test_allowlists_filter_messages() -> None:
    config = FeishuPluginConfig(
        allowed_chats=["oc_allowed"], allowed_users=["ou_allowed"]
    )
    converter = FeishuMessageConverter(config, self_open_id=BOT_OPEN_ID)

    assert await converter.to_inbound(_event(chat_id="oc_other")) is None

    private = await converter.to_inbound(
        _event(chat_type="p2p", chat_id="oc_allowed", sender_open_id="ou_random")
    )
    assert private is None  # p2p sender not in allowed_users

    allowed = await converter.to_inbound(
        _event(chat_type="p2p", chat_id="oc_allowed", sender_open_id="ou_allowed")
    )
    assert allowed is not None


async def test_non_receive_events_ignored() -> None:
    event = _event()
    event["header"]["event_type"] = "im.chat.member.bot.added_v1"

    assert await _converter().to_inbound(event) is None


async def test_empty_content_dropped() -> None:
    inbound = await _converter().to_inbound(_event(content={"text": ""}))

    assert inbound is None


async def test_name_resolver_used_for_display_name() -> None:
    async def resolver(chat_id: str, open_id: str) -> str:
        return "王五"

    converter = _converter(name_resolver=resolver)
    inbound = await converter.to_inbound(_event())

    assert inbound is not None
    assert inbound.sender_context is not None
    assert inbound.sender_context.display_name == "王五"
    assert inbound.message_context is not None
    assert inbound.message_context.sender_display_name == "王五"
    assert inbound.sender_account_key == (
        "feishu:user:ou_user1111111111111111111111111"
    )
