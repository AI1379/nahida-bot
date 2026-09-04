"""Tests for the Feishu channel plugin lifecycle, routing, and sends."""

from __future__ import annotations

import json
from typing import Any

import pytest

from nahida_bot.channels.feishu.client import FeishuAPIError
from nahida_bot.channels.feishu.plugin import FeishuPlugin
from nahida_bot.core.events import MessageObserved, MessageReceived
from nahida_bot.plugins.base import OutboundMessage
from nahida_bot.plugins.manifest import PluginManifest

from .helpers import RecordingMockBotAPI

pytestmark = pytest.mark.asyncio

BOT_OPEN_ID = "ou_bot00000000000000000000000000"
USER_OPEN_ID = "ou_user1111111111111111111111111"
OTHER_OPEN_ID = "ou_other222222222222222222222222"
GROUP_CHAT_ID = "oc_group00000000000000000000000"
P2P_CHAT_ID = "oc_p2p00000000000000000000000000"

_GROUP_MEMBERS = [
    {"member_id": USER_OPEN_ID, "name": "发起者"},
    {"member_id": OTHER_OPEN_ID, "name": "被@的人"},
]


class _FakeClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.replies: list[dict[str, Any]] = []
        self.bot_info_calls = 0
        self.fail_next_post = False
        self.members = list(_GROUP_MEMBERS)
        self.fail_members = False
        self.chat_name = "测试群"

    async def get_bot_info(self) -> dict[str, Any]:
        self.bot_info_calls += 1
        return {"open_id": BOT_OPEN_ID, "activate_status": 1}

    async def send_message(
        self,
        *,
        receive_id_type: str,
        receive_id: str,
        msg_type: str,
        content: str,
        uuid: str = "",
    ) -> dict[str, Any]:
        if self.fail_next_post and msg_type == "post":
            self.fail_next_post = False
            raise FeishuAPIError("too large", api="send_message", code=230025)
        self.sent.append(
            {
                "receive_id_type": receive_id_type,
                "receive_id": receive_id,
                "msg_type": msg_type,
                "content": json.loads(content),
                "uuid": uuid,
            }
        )
        return {"message_id": f"om_{len(self.sent)}"}

    async def reply_message(
        self, *, message_id: str, msg_type: str, content: str, uuid: str = ""
    ) -> dict[str, Any]:
        self.replies.append(
            {
                "message_id": message_id,
                "msg_type": msg_type,
                "content": json.loads(content),
            }
        )
        return {"message_id": "om_reply"}

    async def get_chat_info(self, chat_id: str) -> dict[str, Any]:
        return {"name": self.chat_name, "chat_id": chat_id}

    async def get_chat_members(self, chat_id: str) -> list[dict[str, Any]]:
        if self.fail_members:
            raise FeishuAPIError("no permission", api="get_chat_members", code=230002)
        return list(self.members)


def _manifest(**config_overrides: object) -> PluginManifest:
    config: dict[str, Any] = {"app_id": "cli_x", "app_secret": "s"}
    config.update(config_overrides)
    return PluginManifest(
        id="feishu",
        name="Feishu Channel",
        version="0.1.0",
        entrypoint="nahida_bot.channels.feishu.plugin:FeishuPlugin",
        config=config,
    )


def _event(
    *,
    chat_type: str = "group",
    chat_id: str = GROUP_CHAT_ID,
    text: str = "你好",
    mentions: list[dict[str, Any]] | None = None,
    sender_open_id: str = USER_OPEN_ID,
    sender_type: str = "user",
    message_id: str = "om_msg1",
    message_type: str = "text",
    content: dict[str, Any] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "message_id": message_id,
        "chat_id": chat_id,
        "chat_type": chat_type,
        "message_type": message_type,
        "content": json.dumps(
            content if content is not None else {"text": text}, ensure_ascii=False
        ),
        "create_time": "1609073151345",
    }
    if mentions is not None:
        message["mentions"] = mentions
    return {
        "schema": "2.0",
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "sender": {
                "sender_id": {"open_id": sender_open_id},
                "sender_type": sender_type,
            },
            "message": message,
        },
    }


def _bot_mention() -> list[dict[str, Any]]:
    return [
        {
            "key": "@_user_1",
            "id": {"open_id": BOT_OPEN_ID},
            "mentioned_type": "bot",
            "name": "纳西妲",
        }
    ]


def _plugin(
    **config_overrides: object,
) -> tuple[FeishuPlugin, RecordingMockBotAPI, _FakeClient]:
    api = RecordingMockBotAPI()
    plugin = FeishuPlugin(api, _manifest(**config_overrides))
    fake_client = _FakeClient()
    plugin._client = fake_client  # noqa: SLF001 - test injection
    plugin._rebuild_converters()  # noqa: SLF001
    return plugin, api, fake_client


async def _loaded_plugin(
    **config_overrides: object,
) -> tuple[FeishuPlugin, RecordingMockBotAPI, _FakeClient]:
    plugin, api, client = _plugin(**config_overrides)
    await plugin.on_load()
    return plugin, api, client


async def test_on_load_registers_channel_and_supplement() -> None:
    plugin, api, client = await _loaded_plugin()

    assert api.registered_channels == [plugin]
    assert plugin.self_open_id == BOT_OPEN_ID
    assert client.bot_info_calls == 1
    supplements = api.registered_prompt_supplements
    assert "markdown_rendering" in supplements
    assert supplements["markdown_rendering"]["channel"] == "feishu"


async def test_p2p_message_publishes_received() -> None:
    plugin, api, _ = await _loaded_plugin()

    await plugin.handle_inbound_event(_event(chat_type="p2p", chat_id=P2P_CHAT_ID))

    assert len(api.published_events) == 1
    event = api.published_events[0]
    assert isinstance(event, MessageReceived)
    assert event.payload.session_id == f"feishu:private:{P2P_CHAT_ID}"


async def test_group_mention_publishes_received_with_group_session() -> None:
    plugin, api, _ = await _loaded_plugin()

    await plugin.handle_inbound_event(
        _event(text="@_user_1 在吗", mentions=_bot_mention())
    )

    assert len(api.published_events) == 1
    event = api.published_events[0]
    assert isinstance(event, MessageReceived)
    assert event.payload.session_id == f"feishu:group:{GROUP_CHAT_ID}"
    message = event.payload.message
    assert message.mentions_bot is True
    assert "纳西妲" not in message.text  # self-mention stripped
    # Group display name enriched from chat info.
    assert message.chat_context is not None
    assert message.chat_context.display_name == "测试群"


async def test_group_untriggered_message_filtered_by_default() -> None:
    plugin, api, _ = await _loaded_plugin()

    await plugin.handle_inbound_event(_event(text="随便聊聊"))

    assert api.published_events == []


async def test_group_context_capture_publishes_observed() -> None:
    plugin, api, _ = await _loaded_plugin(group_context_capture=True)

    await plugin.handle_inbound_event(_event(text="随便聊聊"))

    assert len(api.published_events) == 1
    assert isinstance(api.published_events[0], MessageObserved)


async def test_duplicate_message_id_published_once() -> None:
    plugin, api, _ = await _loaded_plugin()

    event = _event(chat_type="p2p", chat_id=P2P_CHAT_ID)
    await plugin.handle_inbound_event(event)
    await plugin.handle_inbound_event(event)

    assert len(api.published_events) == 1


async def test_bot_sender_events_skipped() -> None:
    plugin, api, _ = await _loaded_plugin()

    await plugin.handle_inbound_event(
        _event(chat_type="p2p", chat_id=P2P_CHAT_ID, sender_type="bot")
    )

    assert api.published_events == []


async def test_non_message_events_ignored() -> None:
    plugin, api, _ = await _loaded_plugin()

    event = _event()
    event["header"]["event_type"] = "im.chat.member.bot.added_v1"
    await plugin.handle_inbound_event(event)

    assert api.published_events == []


async def test_send_message_plain_text_uses_chat_id() -> None:
    plugin, _, client = await _loaded_plugin()

    message_id = await plugin.send_message(
        GROUP_CHAT_ID, OutboundMessage(text="普通回复")
    )

    assert message_id == "om_1"
    assert len(client.sent) == 1
    sent = client.sent[0]
    assert sent["receive_id_type"] == "chat_id"
    assert sent["receive_id"] == GROUP_CHAT_ID
    assert sent["msg_type"] == "text"
    assert sent["content"] == {"text": "普通回复"}


async def test_send_message_with_reply_uses_reply_api_first() -> None:
    plugin, _, client = await _loaded_plugin()

    await plugin.send_message(
        GROUP_CHAT_ID,
        OutboundMessage(text="第一段", reply_to="om_9"),
    )

    assert len(client.replies) == 1
    assert client.replies[0]["message_id"] == "om_9"
    assert client.sent == []  # single chunk went entirely through reply


async def test_send_message_reasoning_prepended() -> None:
    plugin, _, client = await _loaded_plugin()

    await plugin.send_message(
        GROUP_CHAT_ID, OutboundMessage(text="正文", reasoning="思考")
    )

    text = client.sent[0]["content"]["text"]
    assert text.startswith("[💭 思考过程]\n思考")
    assert text.endswith("正文")


async def test_send_message_markdown_renders_as_post() -> None:
    plugin, _, client = await _loaded_plugin()

    await plugin.send_message(GROUP_CHAT_ID, OutboundMessage(text="**重点** 内容"))

    assert client.sent[0]["msg_type"] == "post"
    content = client.sent[0]["content"]
    runs = content["post"]["zh_cn"]["content"]
    assert any(run.get("style") == ["bold"] for para in runs for run in para)


async def test_send_message_post_failure_falls_back_to_text() -> None:
    plugin, _, client = await _loaded_plugin()
    client.fail_next_post = True

    message_id = await plugin.send_message(
        GROUP_CHAT_ID, OutboundMessage(text="**重点** 内容")
    )

    assert message_id == "om_1"
    assert client.sent[0]["msg_type"] == "text"
    # Fallback flattens the post AST into readable plain text.
    assert "重点" in client.sent[0]["content"]["text"]
    assert "内容" in client.sent[0]["content"]["text"]


async def test_outbound_mention_validated_and_rendered() -> None:
    plugin, _, client = await _loaded_plugin()
    text = f"[CQ:at,qq={OTHER_OPEN_ID}] 帮我看下这个"

    await plugin.send_message(GROUP_CHAT_ID, OutboundMessage(text=text))

    sent = client.sent[0]
    assert f'<at user_id="{OTHER_OPEN_ID}"></at>' in sent["content"]["text"]


async def test_outbound_mention_unknown_member_stays_literal() -> None:
    plugin, _, client = await _loaded_plugin()
    unknown = "ou_unknown999999999999999999999999"
    text = f"[CQ:at,qq={unknown}] 谁是这个人"

    await plugin.send_message(GROUP_CHAT_ID, OutboundMessage(text=text))

    assert f"[CQ:at,qq={unknown}]" in client.sent[0]["content"]["text"]


async def test_member_fetch_failure_degrades_mentions_safely() -> None:
    plugin, _, client = await _loaded_plugin()
    client.fail_members = True

    text = f"[CQ:at,qq={OTHER_OPEN_ID}] 在吗"
    message_id = await plugin.send_message(GROUP_CHAT_ID, OutboundMessage(text=text))

    assert message_id  # send still succeeds
    assert f"[CQ:at,qq={OTHER_OPEN_ID}]" in client.sent[0]["content"]["text"]


async def test_send_resolves_chat_address_extra() -> None:
    plugin, _, client = await _loaded_plugin()

    await plugin.send_message(
        "ignored",
        OutboundMessage(
            text="定时提醒",
            extra={"chat_address": f"feishu:group:{GROUP_CHAT_ID}"},
        ),
    )

    assert client.sent[0]["receive_id"] == GROUP_CHAT_ID


async def test_send_message_empty_text_with_no_attachments_sends_nothing() -> None:
    plugin, _, client = await _loaded_plugin()

    message_id = await plugin.send_message(GROUP_CHAT_ID, OutboundMessage(text=""))

    assert message_id == ""
    assert client.sent == []
