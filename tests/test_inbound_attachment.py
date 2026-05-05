"""Tests for InboundAttachment dataclass and InboundMessage.attachments."""

from dataclasses import FrozenInstanceError

import pytest

from nahida_bot.plugins.base import InboundAttachment, InboundMessage


class TestInboundAttachment:
    def test_defaults(self) -> None:
        att = InboundAttachment(kind="image", platform_id="res_123")
        assert att.kind == "image"
        assert att.platform_id == "res_123"
        assert att.url == ""
        assert att.path == ""
        assert att.mime_type == ""
        assert att.file_size == 0
        assert att.width == 0
        assert att.height == 0
        assert att.alt_text == ""
        assert att.metadata == {}

    def test_frozen(self) -> None:
        att = InboundAttachment(kind="image", platform_id="res_123")
        with pytest.raises(FrozenInstanceError):
            att.url = "http://example.com/img.jpg"  # type: ignore[misc]

    def test_full_construction(self) -> None:
        att = InboundAttachment(
            kind="image",
            platform_id="res_abc",
            url="http://cdn.example.com/img.jpg",
            width=800,
            height=600,
            mime_type="image/jpeg",
            file_size=102400,
            alt_text="A cute cat",
            metadata={"sub_type": "normal"},
        )
        assert att.url == "http://cdn.example.com/img.jpg"
        assert att.width == 800
        assert att.height == 600
        assert att.mime_type == "image/jpeg"
        assert att.alt_text == "A cute cat"

    def test_slots(self) -> None:
        att = InboundAttachment(kind="image", platform_id="res_123")
        assert not hasattr(att, "__dict__")


class TestInboundMessageAttachments:
    def test_default_empty_attachments(self) -> None:
        msg = InboundMessage(
            message_id="1",
            platform="test",
            chat_id="c1",
            user_id="u1",
            text="hello",
            raw_event={},
        )
        assert msg.attachments == []

    def test_with_attachments(self) -> None:
        att = InboundAttachment(
            kind="image",
            platform_id="res_123",
            url="http://example.com/img.jpg",
            width=100,
            height=100,
        )
        msg = InboundMessage(
            message_id="1",
            platform="milky",
            chat_id="c1",
            user_id="u1",
            text="[Media: type=image, resource_id=res_123]",
            raw_event={},
            attachments=[att],
        )
        assert len(msg.attachments) == 1
        assert msg.attachments[0].platform_id == "res_123"
        assert msg.attachments[0].kind == "image"

    def test_backward_compat_no_attachments(self) -> None:
        """Existing code creating InboundMessage without attachments still works."""
        msg = InboundMessage(
            message_id="1",
            platform="telegram",
            chat_id="c1",
            user_id="u1",
            text="hello",
            raw_event={},
            is_group=False,
            reply_to="",
            timestamp=0.0,
            command_prefix="/",
        )
        assert msg.attachments == []
        assert msg.text == "hello"
