"""Tests for Milky message segment models."""

from __future__ import annotations

from nahida_bot.channels.milky.segments import (
    IncomingFileSegment,
    IncomingForwardSegment,
    IncomingForwardedMessage,
    IncomingImageSegment,
    IncomingMentionSegment,
    IncomingRecordSegment,
    IncomingReplySegment,
    IncomingTextSegment,
    IncomingVideoSegment,
    OutgoingFileUpload,
    OutgoingForwardSegment,
    OutgoingForwardedMessage,
    OutgoingImageSegment,
    OutgoingReplySegment,
    OutgoingTextSegment,
    UnknownIncomingSegment,
    outgoing_segments_to_dicts,
    parse_incoming_forwarded_messages,
    parse_incoming_segment,
    parse_incoming_segments,
    render_segments_plain_text,
)


def test_parse_text_mention_and_media_segments() -> None:
    segments = parse_incoming_segments(
        [
            {"type": "text", "data": {"text": "hello "}},
            {"type": "mention", "data": {"user_id": 123, "name": "Alice"}},
            {
                "type": "image",
                "data": {
                    "resource_id": "img-1",
                    "temp_url": "https://example.com/i.jpg",
                    "width": 640,
                    "height": 480,
                    "summary": "[image]",
                },
            },
            {
                "type": "record",
                "data": {"resource_id": "voice-1", "duration": 5},
            },
            {
                "type": "video",
                "data": {"resource_id": "video-1", "width": 1280, "height": 720},
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

    assert isinstance(segments[0], IncomingTextSegment)
    assert isinstance(segments[1], IncomingMentionSegment)
    assert isinstance(segments[2], IncomingImageSegment)
    assert isinstance(segments[3], IncomingRecordSegment)
    assert isinstance(segments[4], IncomingVideoSegment)
    assert isinstance(segments[5], IncomingFileSegment)

    rendered = render_segments_plain_text(segments)
    assert "hello @Alice" in rendered
    assert "[Media: type=image, resource_id=img-1" in rendered
    assert "temp_url=https://example.com/i.jpg" in rendered
    assert "[Media: type=record, resource_id=voice-1" in rendered
    assert "[Media: type=video, resource_id=video-1" in rendered
    assert "[File: name=report.pdf, file_id=file-1" in rendered


def test_unknown_segment_preserves_raw_data() -> None:
    raw = {"type": "future_type", "data": {"x": 1}}
    segment = parse_incoming_segment(raw)

    assert isinstance(segment, UnknownIncomingSegment)
    assert segment.type == "future_type"
    assert segment.data == {"x": 1}
    assert segment.raw is raw
    assert (
        render_segments_plain_text([segment])
        == "[UnsupportedSegment: type=future_type]"
    )


def test_face_bool_accepts_string_values() -> None:
    segment = parse_incoming_segment(
        {"type": "face", "data": {"face_id": "123", "is_large": "true"}}
    )

    assert getattr(segment, "is_large") is True


def test_forward_segment_can_be_resolved_with_nested_messages() -> None:
    raw_forward = {
        "type": "forward",
        "data": {
            "forward_id": "forward-1",
            "title": "Chat History",
            "preview": ["Alice: hi", "Bob: nested"],
            "summary": "2 messages",
        },
    }
    forward = parse_incoming_segment(raw_forward)
    assert isinstance(forward, IncomingForwardSegment)
    assert forward.is_resolved is False

    forwarded_messages = parse_incoming_forwarded_messages(
        [
            {
                "message_seq": 10,
                "sender_name": "Alice",
                "segments": [{"type": "text", "data": {"text": "hi"}}],
            },
            {
                "message_seq": 11,
                "sender_name": "Bob",
                "segments": [
                    {
                        "type": "forward",
                        "data": {
                            "forward_id": "forward-2",
                            "title": "Nested",
                            "preview": ["Carol: inner"],
                            "summary": "1 message",
                        },
                    }
                ],
            },
        ]
    )

    resolved = forward.with_messages(forwarded_messages)

    assert resolved.is_resolved is True
    rendered = render_segments_plain_text([resolved])
    assert "[Forward: id=forward-1" in rendered
    assert "- Alice: hi" in rendered
    assert "- Bob: [Forward: id=forward-2" in rendered


def test_forward_resolved_state_does_not_depend_on_message_count() -> None:
    forward = IncomingForwardSegment(forward_id="empty")

    resolved = forward.with_messages([])

    assert resolved.is_resolved is True
    assert resolved.messages == []


def test_forward_inside_reply_preserves_depth_limit() -> None:
    inner = IncomingForwardSegment(
        forward_id="inner",
        messages=[
            IncomingForwardedMessage(
                message_seq=1,
                sender_name="Alice",
                segments=[IncomingTextSegment("hidden")],
            )
        ],
    )
    reply = IncomingReplySegment(message_seq=9, segments=[inner])

    rendered = render_segments_plain_text([reply], max_forward_depth=0)

    assert "truncated=true" in rendered
    assert "hidden" not in rendered


def test_nested_forward_render_respects_depth_limit() -> None:
    inner = IncomingForwardSegment(
        forward_id="inner",
        messages=[
            IncomingForwardedMessage(
                message_seq=1,
                sender_name="Carol",
                segments=[IncomingTextSegment(text="secret")],
            )
        ],
    )
    outer = IncomingForwardSegment(
        forward_id="outer",
        messages=[
            IncomingForwardedMessage(
                message_seq=2,
                sender_name="Bob",
                segments=[inner],
            )
        ],
    )

    rendered = render_segments_plain_text([outer], max_forward_depth=1)

    assert "id=inner" in rendered
    assert "truncated=true" in rendered
    assert "secret" not in rendered


def test_outgoing_forward_serializes_nested_forward_segments() -> None:
    nested = OutgoingForwardSegment(
        messages=[
            OutgoingForwardedMessage(
                user_id=10002,
                sender_name="Bob",
                segments=[OutgoingTextSegment("nested text")],
            )
        ],
        title="Nested",
    )
    forward = OutgoingForwardSegment(
        messages=[
            OutgoingForwardedMessage(
                user_id=10001,
                sender_name="Alice",
                segments=[
                    OutgoingTextSegment("hello"),
                    nested,
                    OutgoingImageSegment(uri="file:///tmp/a.png", summary="[image]"),
                ],
            )
        ],
        title="Chat History",
        preview=["Alice: hello"],
        summary="1 message",
        prompt="[Merged Forward]",
    )

    payload = outgoing_segments_to_dicts([OutgoingReplySegment(123), forward])

    assert payload[0] == {"type": "reply", "data": {"message_seq": 123}}
    assert payload[1]["type"] == "forward"
    assert payload[1]["data"]["messages"][0]["segments"][1]["type"] == "forward"
    assert payload[1]["data"]["messages"][0]["segments"][2]["type"] == "image"


def test_file_upload_payloads_are_not_message_segments() -> None:
    upload = OutgoingFileUpload(
        file_uri="file:///tmp/report.pdf",
        file_name="report.pdf",
        parent_folder_id="folder-1",
    )

    assert upload.private_payload(10001) == {
        "user_id": 10001,
        "file_uri": "file:///tmp/report.pdf",
        "file_name": "report.pdf",
    }
    assert upload.group_payload(20001) == {
        "group_id": 20001,
        "parent_folder_id": "folder-1",
        "file_uri": "file:///tmp/report.pdf",
        "file_name": "report.pdf",
    }
