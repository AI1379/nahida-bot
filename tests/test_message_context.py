"""Unit tests for nahida_bot.core.message_context — envelope prefix stripping."""

import pytest

from nahida_bot.core.message_context import (
    render_envelope,
    render_message_with_context,
    strip_envelope_prefix,
)
from nahida_bot.plugins.base import MessageContext


class TestStripEnvelopePrefixTimestampOnly:
    """Bare timestamp prefix (no brackets)."""

    def test_bare_timestamp_with_tz(self):
        assert strip_envelope_prefix("2026-05-10 14:03 +08\nHello!") == "Hello!"

    def test_bare_timestamp_with_tz_inline(self):
        assert strip_envelope_prefix("2026-05-10 14:03 +08 Hello!") == "Hello!"

    def test_bare_timestamp_without_tz(self):
        assert strip_envelope_prefix("2026-05-10 14:03\nHello!") == "Hello!"

    def test_bare_timestamp_with_tz_colon(self):
        assert strip_envelope_prefix("2026-05-10 14:03 +08:00\nHello!") == "Hello!"

    def test_bare_timestamp_with_tz_4digit(self):
        assert strip_envelope_prefix("2026-05-10 14:03 +0800\nHello!") == "Hello!"

    def test_bare_timestamp_with_negative_tz(self):
        assert strip_envelope_prefix("2026-05-10 14:03 -05:00\nHello!") == "Hello!"

    def test_bare_timestamp_with_seconds(self):
        assert strip_envelope_prefix("2026-05-10 14:03:45 +08\nHello!") == "Hello!"

    def test_iso_format_with_T_separator(self):
        assert strip_envelope_prefix("2026-05-10T14:03+08:00\nHello!") == "Hello!"


class TestStripEnvelopePrefixBracketed:
    """Timestamp wrapped in square brackets."""

    def test_bracketed_timestamp(self):
        assert strip_envelope_prefix("[2026-05-10 14:03 +08] Hello!") == "Hello!"

    def test_bracketed_timestamp_newline(self):
        assert strip_envelope_prefix("[2026-05-10 14:03 +08]\nHello!") == "Hello!"


class TestStripEnvelopePrefixFullEnvelope:
    """Full envelope bracket with channel/sender info."""

    def test_full_envelope(self):
        assert (
            strip_envelope_prefix(
                "[2026-05-10 14:03 +08 | milky/group:Chat(123) | Alice admin]\nHello!"
            )
            == "Hello!"
        )

    def test_full_envelope_inline(self):
        assert (
            strip_envelope_prefix("[2026-05-10 14:03 +08 | milky/group | Bob] Hello!")
            == "Hello!"
        )

    def test_full_envelope_no_tz(self):
        assert (
            strip_envelope_prefix(
                "[2026-05-10 14:03 | milky/private:456 | Charlie]\nHello!"
            )
            == "Hello!"
        )


class TestStripEnvelopePrefixStructuredContext:
    """Structured context blocks emitted by the model are removed too."""

    def test_context_block_prefix(self):
        text = (
            '<message_context trust="untrusted" role="user">\n'
            "timestamp: 2026-05-10 14:03 +08\n"
            "channel: milky/group\n"
            "sender: Alice\n"
            "text:\n"
            "  hi\n"
            "</message_context>\n"
            "Hello!"
        )

        assert strip_envelope_prefix(text) == "Hello!"

    def test_markdown_quote_before_full_envelope(self):
        assert (
            strip_envelope_prefix(
                "> [2026-05-10 14:03 +08 | milky/group | Alice]\nHello!"
            )
            == "Hello!"
        )

    def test_markdown_list_before_full_envelope(self):
        assert (
            strip_envelope_prefix(
                "- [2026-05-10 14:03 +08 | milky/group | Alice]\nHello!"
            )
            == "Hello!"
        )


class TestRenderMessageWithContext:
    """LLM-visible context rendering."""

    def test_user_message_renders_structured_untrusted_block(self):
        ctx = MessageContext(
            timestamp=1778847454.0,
            channel="milky",
            chat_type="group",
            chat_id="123",
            chat_display_name="Chat",
            sender_id="u1",
            sender_display_name="Alice",
            sender_role_tags=("admin",),
        )

        rendered = render_message_with_context("Hello\nworld", ctx, role="user")

        assert rendered.startswith('<message_context trust="untrusted" role="user">\n')
        assert "timestamp: " in rendered
        assert "channel: milky/group:Chat(123)" in rendered
        assert "sender: Alice(u1) admin" in rendered
        assert "text:\n  Hello\n  world" in rendered
        assert rendered.endswith("</message_context>")

    def test_assistant_message_does_not_render_metadata_block(self):
        ctx = MessageContext(
            channel="bot",
            chat_type="assistant",
            sender_display_name="bot",
        )

        assert (
            render_message_with_context("Clean assistant text", ctx, role="assistant")
            == "Clean assistant text"
        )

    def test_render_envelope_returns_metadata_only_block(self):
        ctx = MessageContext(channel="test", chat_type="private", chat_id="c1")

        rendered = render_envelope(ctx, role="user")

        assert rendered.startswith('<message_context trust="untrusted" role="user">\n')
        assert "channel: test/private:c1" in rendered
        assert "text:" not in rendered

    @pytest.mark.parametrize("mentions_bot", [False, True])
    def test_platform_mentions_are_explicit_even_with_same_name(self, mentions_bot):
        ctx = MessageContext(
            channel="milky",
            chat_type="group",
            sender_id="human",
            sender_display_name="Alice",
            message_id="reply",
            reply_to_message_id="other-bot-message",
            mentions_bot=mentions_bot,
            mentioned_user_ids=("other-bot", "another-user"),
        )

        rendered = render_message_with_context("@纳西妲 不是在夸你", ctx, role="user")

        assert "sender: Alice(human)" in rendered
        assert "reply_to_message_id: other-bot-message" in rendered
        assert f"mentions_bot: {'yes' if mentions_bot else 'no'}" in rendered
        assert "mentioned_user_ids: other-bot, another-user" in rendered

    @pytest.mark.parametrize("max_chars", [100, 400])
    def test_budget_truncates_text_without_losing_attribution(self, max_chars):
        ctx = MessageContext(
            channel="test",
            chat_type="group",
            sender_id="other-bot",
            sender_display_name="纳西妲",
            message_id="quoted",
            reply_to_message_id="earlier",
            mentioned_user_ids=("someone-else",),
        )

        rendered = render_message_with_context(
            "很长的消息\n" * 1000, ctx, role="batch_message", max_chars=max_chars
        )

        assert "sender: 纳西妲(other-bot)" in rendered
        assert "message_id: quoted" in rendered
        assert "reply_to_message_id: earlier" in rendered
        assert "mentions_bot: no" in rendered
        assert "mentioned_user_ids: someone-else" in rendered
        assert "[message text truncated]" in rendered
        assert rendered.endswith("</message_context>")
        if max_chars == 400:
            assert len(rendered) <= max_chars

    def test_legacy_message_without_metadata_also_obeys_budget(self):
        rendered = render_message_with_context("x" * 1000, None, max_chars=100)
        assert len(rendered) <= 100
        assert "[message text truncated]" in rendered


class TestStripEnvelopePrefixEdgeCases:
    """Edge cases and no-op scenarios."""

    def test_empty_string(self):
        assert strip_envelope_prefix("") == ""

    def test_plain_text_no_timestamp(self):
        text = "Hello, how are you?"
        assert strip_envelope_prefix(text) == text

    def test_text_with_mid_timestamp(self):
        """Timestamp in the middle should NOT be stripped."""
        text = "See you at 2026-05-10 14:03 +08 then!"
        assert strip_envelope_prefix(text) == text

    def test_timestamp_only_strips_to_empty(self):
        """If the entire text is a timestamp, result is empty after lstrip."""
        result = strip_envelope_prefix("2026-05-10 14:03 +08")
        assert result == ""

    def test_bracketed_timestamp_only(self):
        result = strip_envelope_prefix("[2026-05-10 14:03 +08]")
        assert result == ""

    def test_leading_whitespace_before_timestamp(self):
        assert strip_envelope_prefix("  2026-05-10 14:03 +08\nHello!") == "Hello!"

    def test_multiline_reply_after_timestamp(self):
        result = strip_envelope_prefix("2026-05-10 14:03 +08\nLine 1\nLine 2")
        assert result == "Line 1\nLine 2"

    def test_preserves_non_timestamp_start(self):
        """Text that merely looks date-ish but doesn't start with it."""
        text = "Let's meet on 2026-05-10 at 14:03."
        assert strip_envelope_prefix(text) == text

    def test_timestamp_with_dot_separator(self):
        result = strip_envelope_prefix("2026-05-10 14:03 +08. Hello!")
        assert result == "Hello!"

    def test_timestamp_with_comma_separator(self):
        result = strip_envelope_prefix("2026-05-10 14:03 +08, Hello!")
        assert result == "Hello!"
