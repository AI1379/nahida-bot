"""Tests for Milky outbound message conversion."""

from __future__ import annotations

from nahida_bot.channels.milky.config import parse_milky_config
from nahida_bot.channels.milky.segment_converter import (
    MilkyTargetError,
    MilkyOutboundConverter,
    message_seq_from_send_result,
    resolve_target,
)
from nahida_bot.channels.milky.segments import (
    OutgoingFileUpload,
    OutgoingForwardSegment,
    OutgoingImageSegment,
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
