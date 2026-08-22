"""Tests for the Discord message converter (dict → InboundMessage)."""

from __future__ import annotations

from typing import Any

from nahida_bot.channels.discord.message_converter import (
    AttachmentUrlCache,
    classify_chat,
)
from nahida_bot.channels.discord.plugin import DiscordMessageConverter


def _message(**overrides: Any) -> dict[str, Any]:
    message: dict[str, Any] = {
        "id": "1001",
        "type": "default",
        "content": "hello",
        "timestamp": 1737000000.0,
        "author": {
            "id": "42",
            "name": "alice",
            "display_name": "Alice",
            "bot": False,
        },
        "guild_id": "777",
        "channel": {
            "id": "111",
            "type": "text",
            "name": "general",
            "guild_id": "777",
            "parent_id": "",
        },
        "mentions": [],
        "mention_everyone": False,
        "attachments": [],
        "embed_count": 0,
        "reference_message_id": "",
    }
    message.update(overrides)
    return message


def _dm_message(**overrides: Any) -> dict[str, Any]:
    return _message(
        guild_id="",
        channel={
            "id": "500",
            "type": "dm",
            "name": "",
            "guild_id": "",
            "parent_id": "",
        },
        **overrides,
    )


def _thread_message(**overrides: Any) -> dict[str, Any]:
    return _message(
        channel={
            "id": "333",
            "type": "public_thread",
            "name": "big question",
            "guild_id": "777",
            "parent_id": "111",
        },
        **overrides,
    )


class TestClassifyChat:
    def test_dm_is_private(self) -> None:
        assert classify_chat(_dm_message()) == ("private", False)

    def test_group_dm_is_private(self) -> None:
        message = _dm_message()
        message["channel"]["type"] = "group_dm"
        assert classify_chat(message) == ("private", False)

    def test_guild_text_channel(self) -> None:
        assert classify_chat(_message()) == ("channel", True)

    def test_public_thread(self) -> None:
        assert classify_chat(_thread_message()) == ("thread", True)

    def test_private_thread(self) -> None:
        message = _thread_message()
        message["channel"]["type"] = "private_thread"
        assert classify_chat(message) == ("thread", True)

    def test_forum_post_is_thread(self) -> None:
        # Forum posts arrive as public threads whose parent is a forum channel.
        message = _thread_message()
        message["channel"]["type"] = "public_thread"
        assert classify_chat(message) == ("thread", True)


class TestConverterAddressing:
    def test_dm_maps_to_private(self) -> None:
        inbound = DiscordMessageConverter().to_inbound(_dm_message())

        assert inbound.is_group is False
        assert inbound.chat_id == "500"
        assert inbound.chat_context is not None
        assert inbound.chat_context.chat_type == "private"
        assert inbound.platform == "discord"

    def test_guild_channel_maps_to_channel_type(self) -> None:
        inbound = DiscordMessageConverter().to_inbound(_message())

        assert inbound.is_group is True
        assert inbound.chat_id == "111"
        assert inbound.chat_context is not None
        assert inbound.chat_context.chat_type == "channel"
        assert inbound.chat_context.display_name == "#general"

    def test_thread_maps_to_thread_type_with_thread_id(self) -> None:
        inbound = DiscordMessageConverter().to_inbound(_thread_message())

        assert inbound.is_group is True
        assert inbound.chat_id == "333"
        assert inbound.chat_context is not None
        assert inbound.chat_context.chat_type == "thread"
        # Thread names render with the parent channel for context.
        assert "big question" in inbound.chat_context.display_name

    def test_thread_session_address_round_trips(self) -> None:
        from nahida_bot.core.chat_address import ChatAddress

        inbound = DiscordMessageConverter().to_inbound(_thread_message())
        address = ChatAddress.parse(f"discord:thread:{inbound.chat_id}")
        assert address.target_type == "thread"
        assert address.target_id == "333"

    def test_message_context_carries_chat_facts(self) -> None:
        inbound = DiscordMessageConverter().to_inbound(_message())

        assert inbound.message_context is not None
        assert inbound.message_context.chat_type == "channel"
        assert inbound.message_context.chat_id == "111"
        assert inbound.message_context.sender_id == "42"


class TestConverterMentions:
    def test_bot_mention_sets_mentions_bot(self) -> None:
        message = _message(
            content="<@999> help me",
            mentions=[{"id": "999", "name": "Nahida"}],
        )
        inbound = DiscordMessageConverter(bot_user_id="999").to_inbound(message)

        assert inbound.mentions_bot is True
        assert inbound.mentioned_user_ids == ("999",)

    def test_other_user_mention_does_not_wake_bot(self) -> None:
        message = _message(
            content="hi <@555>",
            mentions=[{"id": "555", "name": "Bob"}],
        )
        inbound = DiscordMessageConverter(bot_user_id="999").to_inbound(message)

        assert inbound.mentions_bot is False
        assert inbound.mentioned_user_ids == ("555",)

    def test_mention_everyone_does_not_wake_bot(self) -> None:
        message = _message(content="@everyone party", mention_everyone=True)
        inbound = DiscordMessageConverter(bot_user_id="999").to_inbound(message)

        assert inbound.mentions_bot is False

    def test_mention_tokens_rewritten_to_names(self) -> None:
        message = _message(
            content="hey <@555> look",
            mentions=[{"id": "555", "name": "Bob"}],
        )
        inbound = DiscordMessageConverter().to_inbound(message)

        assert inbound.text == "hey @Bob look"

    def test_legacy_mention_token_form_rewritten(self) -> None:
        message = _message(
            content="hey <@!555> look",
            mentions=[{"id": "555", "name": "Bob"}],
        )
        inbound = DiscordMessageConverter().to_inbound(message)

        assert inbound.text == "hey @Bob look"

    def test_leading_bot_mention_stripped(self) -> None:
        message = _message(
            content="<@999> /help",
            mentions=[{"id": "999", "name": "Nahida"}],
        )
        inbound = DiscordMessageConverter(
            bot_user_id="999", bot_username="Nahida"
        ).to_inbound(message)

        assert inbound.text == "/help"
        assert inbound.mentions_bot is True

    def test_leading_bot_mention_stripped_by_id_fallback(self) -> None:
        message = _message(
            content="<@999> /help",
            mentions=[{"id": "999", "name": "Nahida"}],
        )
        inbound = DiscordMessageConverter(bot_user_id="999").to_inbound(message)

        assert inbound.text == "/help"


class TestConverterAttachments:
    def test_attachment_kinds_by_mime(self) -> None:
        message = _message(
            content="",
            attachments=[
                {
                    "id": "a1",
                    "filename": "cat.png",
                    "content_type": "image/png",
                    "size": 123,
                    "url": "https://cdn.example/a1",
                    "width": 10,
                    "height": 20,
                    "spoiler": False,
                },
                {
                    "id": "a2",
                    "filename": "song.mp3",
                    "content_type": "audio/mpeg",
                    "size": 456,
                    "url": "https://cdn.example/a2",
                },
                {
                    "id": "a3",
                    "filename": "doc.pdf",
                    "content_type": "application/pdf",
                    "size": 789,
                    "url": "https://cdn.example/a3",
                },
            ],
        )
        inbound = DiscordMessageConverter().to_inbound(message)

        assert [a.kind for a in inbound.attachments] == ["image", "audio", "file"]
        assert inbound.attachments[0].platform_id == "a1"
        assert inbound.attachments[0].url == "https://cdn.example/a1"
        assert inbound.attachments[0].width == 10
        assert inbound.attachments[2].mime_type == "application/pdf"
        # Marker lines give the agent the download ids.
        assert "[Attachment: name=cat.png, type=image, id=a1]" in inbound.text
        assert "[Attachment: name=doc.pdf, type=file, id=a3]" in inbound.text

    def test_attachment_marker_appended_to_existing_text(self) -> None:
        message = _message(
            content="look at this",
            attachments=[
                {
                    "id": "a1",
                    "filename": "cat.png",
                    "content_type": "image/png",
                    "size": 1,
                    "url": "https://cdn.example/a1",
                }
            ],
        )
        inbound = DiscordMessageConverter().to_inbound(message)

        assert inbound.text.startswith("look at this")
        assert "[Attachment: name=cat.png, type=image, id=a1]" in inbound.text


class TestConverterMisc:
    def test_reply_reference_preserved(self) -> None:
        message = _message(reference_message_id="888")
        inbound = DiscordMessageConverter().to_inbound(message)

        assert inbound.reply_to == "888"

    def test_sender_display_name_prefers_display(self) -> None:
        inbound = DiscordMessageConverter().to_inbound(_message())
        assert inbound.sender_context is not None
        assert inbound.sender_context.display_name == "Alice"
        assert inbound.sender_context.platform_user_id == "42"

    def test_raw_event_is_message_dict(self) -> None:
        message = _message()
        inbound = DiscordMessageConverter().to_inbound(message)

        assert inbound.raw_event is message
        assert inbound.command_prefix == "/"


class TestAttachmentUrlCache:
    def test_record_and_get(self) -> None:
        cache = AttachmentUrlCache()
        cache.record(
            [
                {
                    "id": "a1",
                    "filename": "f.png",
                    "content_type": "image/png",
                    "url": "https://cdn.example/a1",
                }
            ]
        )

        entry = cache.get("a1")
        assert entry is not None
        assert entry["url"] == "https://cdn.example/a1"
        assert entry["filename"] == "f.png"

    def test_unknown_id_returns_none(self) -> None:
        assert AttachmentUrlCache().get("nope") is None

    def test_evicts_oldest_beyond_capacity(self) -> None:
        cache = AttachmentUrlCache(capacity=2)
        for i in range(3):
            cache.record(
                [{"id": f"a{i}", "filename": "", "content_type": "", "url": f"u{i}"}]
            )

        assert cache.get("a0") is None
        assert cache.get("a2") is not None

    def test_skips_entries_without_id_or_url(self) -> None:
        cache = AttachmentUrlCache()
        cache.record([{"id": "", "url": "u"}, {"id": "x", "url": ""}])

        assert cache.get("") is None
        assert cache.get("x") is None
