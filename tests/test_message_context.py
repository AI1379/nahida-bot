"""Unit tests for nahida_bot.core.message_context — envelope prefix stripping."""

from nahida_bot.core.message_context import strip_envelope_prefix


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
