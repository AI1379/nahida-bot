"""Tests for Milky outbound message conversion."""

from __future__ import annotations

import pytest

from nahida_bot.channels.milky.config import parse_milky_config
from nahida_bot.channels.milky.segment_converter import (
    MilkyTargetError,
    MilkyOutboundConverter,
    fallback_text_for_segments,
    has_rich_segments,
    message_seq_from_send_result,
    resolve_target,
    video_segment_to_file_upload,
)
from nahida_bot.channels.milky.segments import (
    OutgoingFileUpload,
    OutgoingForwardSegment,
    OutgoingImageSegment,
    OutgoingMentionSegment,
    OutgoingRecordSegment,
    OutgoingReplySegment,
    OutgoingTextSegment,
    OutgoingVideoSegment,
)
from nahida_bot.plugins.base import Attachment, OutboundMessage


def test_converts_text_reply_and_media_attachments() -> None:
    converter = MilkyOutboundConverter(parse_milky_config({}))
    message = OutboundMessage(
        text="hello",
        reply_to="42",
        attachments=[
            Attachment(type="photo", path="file:///tmp/a.png", caption="[image]"),
            Attachment(type="voice", path="file:///tmp/a.ogg"),
            Attachment(type="video", path="file:///tmp/a.mp4"),
        ],
    )

    segments, files = converter.to_payload(message)

    assert files == []
    assert isinstance(segments[0], OutgoingReplySegment)
    assert isinstance(segments[1], OutgoingTextSegment)
    assert isinstance(segments[2], OutgoingImageSegment)
    assert isinstance(segments[3], OutgoingRecordSegment)
    assert isinstance(segments[4], OutgoingVideoSegment)


def test_converts_document_attachment_to_file_upload() -> None:
    converter = MilkyOutboundConverter(parse_milky_config({}))

    segments, files = converter.to_payload(
        OutboundMessage(
            text="",
            attachments=[
                Attachment(
                    type="document",
                    path="file:///tmp/report.pdf",
                    filename="report.pdf",
                )
            ],
        )
    )

    assert segments == []
    assert len(files) == 1
    assert isinstance(files[0], OutgoingFileUpload)
    assert files[0].file_name == "report.pdf"


def test_converts_extra_forward_and_raw_media_segments() -> None:
    converter = MilkyOutboundConverter(parse_milky_config({}))

    segments, files = converter.to_payload(
        OutboundMessage(
            text="",
            extra={
                "milky_segments": [
                    {"type": "image", "data": {"uri": "file:///tmp/a.png"}},
                    {"type": "record", "data": {"uri": "file:///tmp/a.ogg"}},
                ],
                "milky_forward": {
                    "title": "History",
                    "messages": [
                        {
                            "user_id": 10001,
                            "sender_name": "Alice",
                            "text": "hello",
                        },
                        {
                            "user_id": 10002,
                            "sender_name": "Bob",
                            "segments": [{"type": "text", "data": {"text": "world"}}],
                        },
                    ],
                },
            },
        )
    )

    assert files == []
    assert isinstance(segments[0], OutgoingImageSegment)
    assert isinstance(segments[1], OutgoingRecordSegment)
    assert isinstance(segments[2], OutgoingForwardSegment)
    payload = segments[2].to_dict()
    assert payload["type"] == "forward"
    assert payload["data"]["messages"][0]["segments"][0]["data"]["text"] == "hello"


def test_splits_long_text() -> None:
    converter = MilkyOutboundConverter(parse_milky_config({"max_text_length": 3}))

    segments, files = converter.to_payload(OutboundMessage(text="abcdefg"))

    assert files == []
    assert [
        segment.text for segment in segments if isinstance(segment, OutgoingTextSegment)
    ] == [
        "abc",
        "def",
        "g",
    ]


def test_resolve_target_prefers_explicit_extra() -> None:
    scene, peer_id = resolve_target(
        "friend:1",
        OutboundMessage(
            text="hi", extra={"message_scene": "group", "peer_id": "20001"}
        ),
    )

    assert scene == "group"
    assert peer_id == 20001


def test_resolve_target_prefix_and_scene_memory() -> None:
    assert resolve_target("group:20001", OutboundMessage(text="hi")) == (
        "group",
        20001,
    )
    assert resolve_target(
        "20001", OutboundMessage(text="hi"), scene_by_peer={"20001": "group"}
    ) == ("group", 20001)


def test_resolve_target_uses_chat_address_metadata() -> None:
    assert resolve_target(
        "20001",
        OutboundMessage(text="hi", extra={"chat_address": "milky:group:20001"}),
    ) == ("group", 20001)


def test_resolve_target_requires_scene_without_metadata() -> None:
    with pytest.raises(MilkyTargetError, match="requires explicit chat type"):
        resolve_target("20001", OutboundMessage(text="hi"))


def test_message_seq_from_send_result() -> None:
    assert message_seq_from_send_result({"message_seq": 123}) == "123"
    assert message_seq_from_send_result({"file_id": "abc"}) == "abc"
    assert message_seq_from_send_result({}) == ""


def test_resolve_target_rejects_invalid_target() -> None:
    try:
        resolve_target("not-a-number", OutboundMessage(text="hi"))
    except MilkyTargetError as exc:
        assert "not-a-number" in str(exc)
    else:
        raise AssertionError("resolve_target should reject invalid peer IDs")


@pytest.mark.parametrize(
    "uri, expected_name",
    [
        ("file:///tmp/a.mp4", "a.mp4"),
        ("https://host/path/clip.mp4?token=x", "clip.mp4"),
        ("file:///tmp/noext", "video.mp4"),
        ("file:///tmp/../hidden", "video.mp4"),
    ],
)
def test_video_segment_to_file_upload_derives_filename(
    uri: str, expected_name: str
) -> None:
    upload = video_segment_to_file_upload(OutgoingVideoSegment(uri=uri))

    assert upload.file_uri == uri
    assert upload.file_name == expected_name


def test_converts_validated_mention_tokens_in_order() -> None:
    converter = MilkyOutboundConverter(parse_milky_config({}))
    message = OutboundMessage(
        text="[CQ:at,qq=111] 你先说，@[qq=222] 你补充",
        extra={"milky_mention_ids": ["111", "222"]},
    )

    segments, files = converter.to_payload(message)

    assert files == []
    assert [type(seg) for seg in segments] == [
        OutgoingMentionSegment,
        OutgoingTextSegment,
        OutgoingMentionSegment,
        OutgoingTextSegment,
    ]
    assert segments[0].user_id == 111
    assert segments[2].user_id == 222
    assert segments[1].text == " 你先说，"
    assert segments[3].text == " 你补充"


def test_unvalidated_mention_token_stays_literal() -> None:
    converter = MilkyOutboundConverter(parse_milky_config({}))
    message = OutboundMessage(
        text="[CQ:at,qq=999] hi @[qq=111] ok",
        extra={"milky_mention_ids": ["111"]},
    )

    segments, _ = converter.to_payload(message)

    assert [type(seg) for seg in segments] == [
        OutgoingTextSegment,
        OutgoingMentionSegment,
        OutgoingTextSegment,
    ]
    assert segments[0].text == "[CQ:at,qq=999] hi "
    assert segments[1].user_id == 111
    assert segments[2].text == " ok"


def test_tokens_without_validation_metadata_stay_literal() -> None:
    converter = MilkyOutboundConverter(parse_milky_config({}))
    message = OutboundMessage(text="[CQ:at,qq=111] hello")

    segments, _ = converter.to_payload(message)

    assert [type(seg) for seg in segments] == [OutgoingTextSegment]
    assert segments[0].text == "[CQ:at,qq=111] hello"


def test_long_text_split_never_cuts_a_mention_token() -> None:
    converter = MilkyOutboundConverter(parse_milky_config({"max_text_length": 5}))
    message = OutboundMessage(
        text="aaaaaabbb[CQ:at,qq=1]cc",
        extra={"milky_mention_ids": ["1"]},
    )

    segments, _ = converter.to_payload(message)

    assert [type(seg) for seg in segments] == [
        OutgoingTextSegment,
        OutgoingTextSegment,
        OutgoingMentionSegment,
        OutgoingTextSegment,
    ]
    assert segments[0].text == "aaaaa"
    assert segments[1].text == "abbb"
    assert segments[3].text == "cc"


def test_extra_raw_segment_supports_mention_type() -> None:
    converter = MilkyOutboundConverter(parse_milky_config({}))
    message = OutboundMessage(
        text="",
        extra={
            "milky_segments": [
                {"type": "mention", "data": {"user_id": 314}},
            ]
        },
    )

    segments, _ = converter.to_payload(message)

    assert isinstance(segments[0], OutgoingMentionSegment)
    assert segments[0].user_id == 314


def test_fallback_text_renders_mention_as_plain_at() -> None:
    text = fallback_text_for_segments(
        [
            OutgoingMentionSegment(user_id=271),
            OutgoingTextSegment(text="看一下"),
        ]
    )

    assert text == "@271\n看一下"


def test_mention_is_not_a_rich_segment() -> None:
    assert has_rich_segments([OutgoingMentionSegment(user_id=1)]) is False
    assert has_rich_segments([OutgoingImageSegment(uri="file:///a")]) is True
