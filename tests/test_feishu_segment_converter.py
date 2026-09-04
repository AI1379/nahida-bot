"""Tests for the Feishu outbound converter and target resolution."""

from __future__ import annotations

import json

from nahida_bot.channels.feishu.config import FeishuPluginConfig
from nahida_bot.channels.feishu.segment_converter import (
    FeishuOutboundConverter,
    FeishuTargetError,
    message_id_from_send_result,
    resolve_target,
)
from nahida_bot.plugins.base import Attachment, OutboundMessage


def _converter(**config_kwargs: object) -> FeishuOutboundConverter:
    return FeishuOutboundConverter(FeishuPluginConfig(**config_kwargs))


def _message(text: str = "", **extra: object) -> OutboundMessage:
    return OutboundMessage(text=text, extra=dict(extra))


# ── resolve_target ────────────────────────────────────────────


def test_resolve_from_extra_chat_address() -> None:
    message = _message(chat_address="feishu:group:oc_group1")

    assert resolve_target("oc_group1", message) == ("chat_id", "oc_group1")

    message = _message(chat_address="feishu:private:ou_direct")
    assert resolve_target("whatever", message) == ("open_id", "ou_direct")


def test_resolve_from_typed_target_string() -> None:
    assert resolve_target("feishu:group:oc_g", _message()) == ("chat_id", "oc_g")
    assert resolve_target("feishu:private:ou_u", _message()) == ("open_id", "ou_u")


def test_resolve_bare_target_by_prefix() -> None:
    assert resolve_target("ou_abc", _message()) == ("open_id", "ou_abc")
    assert resolve_target("on_abc", _message()) == ("union_id", "on_abc")
    assert resolve_target("oc_abc", _message()) == ("chat_id", "oc_abc")


def test_resolve_rejects_foreign_channel_address() -> None:
    try:
        resolve_target("milky:group:123", _message())
    except FeishuTargetError:
        pass
    else:
        raise AssertionError("expected FeishuTargetError")


# ── payload conversion ────────────────────────────────────────


def test_plain_text_without_markdown_sent_as_text() -> None:
    payload = _converter().to_payload(_message("普通文本回复"))

    assert len(payload.items) == 1
    item = payload.items[0]
    assert item.kind == "text"
    assert json.loads(item.content) == {"text": "普通文本回复"}
    assert payload.reply_to == ""


def test_markdown_sent_as_post() -> None:
    payload = _converter().to_payload(_message("说明：\n\n**重点** 和 `code`"))

    assert payload.items[0].kind == "post"
    content = json.loads(payload.items[0].content)
    runs = content["post"]["zh_cn"]["content"]
    assert any(run.get("style") == ["bold"] for para in runs for run in para)


def test_markdown_disabled_sends_plain_text() -> None:
    payload = _converter(markdown_enabled=False).to_payload(_message("**重点**"))

    assert payload.items[0].kind == "text"
    assert json.loads(payload.items[0].content) == {"text": "**重点**"}


def test_long_text_split_into_chunks() -> None:
    text = "\n\n".join(f"第{i}段 " + "字" * 90 for i in range(6))
    payload = _converter(max_text_length=200).to_payload(_message(text))

    assert len(payload.items) > 1
    for item in payload.items:
        assert len(json.loads(item.content)["text"]) <= 200


def test_validated_mentions_become_at_tags() -> None:
    open_id = "ou_84aad35d084aa403a838cf73ee18467"
    message = _message(
        f"[CQ:at,qq={open_id}] 收一下文件，另一个 [CQ:at,qq=ou_unknown0000000000000000] 未验证",
        feishu_mention_ids=[open_id],
    )
    payload = _converter().to_payload(message)
    text = json.loads(payload.items[0].content)["text"]

    assert f'<at user_id="{open_id}"></at>' in text
    assert "[CQ:at,qq=ou_unknown0000000000000000]" in text  # degraded stays literal


def test_attachments_mapped_to_upload_items() -> None:
    message = OutboundMessage(
        text="给你文件",
        attachments=[
            Attachment(type="photo", path="/tmp/pic.png"),
            Attachment(type="document", path="/tmp/doc.pdf", filename="报告.pdf"),
            Attachment(type="audio", path="/tmp/voice.opus"),
            Attachment(type="audio", path="/tmp/voice.mp3"),
            Attachment(type="video", path="/tmp/clip.mkv"),
        ],
    )
    payload = _converter().to_payload(message)

    kinds = [item.kind for item in payload.items]
    file_types = [item.file_type for item in payload.items if item.kind == "file"]

    assert kinds == ["text", "image", "file", "file", "file", "file"]
    assert file_types == ["pdf", "opus", "stream", "stream"]


def test_reply_to_propagated() -> None:
    payload = _converter().to_payload(OutboundMessage(text="回复", reply_to="om_9"))

    assert payload.reply_to == "om_9"


def test_message_id_from_send_result_shapes() -> None:
    assert message_id_from_send_result({"data": {"message_id": "om_5"}}) == "om_5"
    assert message_id_from_send_result({"message_id": "om_6"}) == "om_6"
    assert message_id_from_send_result({}) == ""
