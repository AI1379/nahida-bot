"""Tests for the Markdown → Telegram HTML converter."""

from __future__ import annotations

from nahida_bot.channels.telegram.markdown_converter import (
    TELEGRAM_MSG_LIMIT,
    convert_markdown_to_telegram_html,
    split_html_message,
)


# ── convert_markdown_to_telegram_html ──────────────────────────────


class TestBold:
    def test_bold(self) -> None:
        assert convert_markdown_to_telegram_html("**bold**") == "<b>bold</b>"

    def test_bold_in_sentence(self) -> None:
        result = convert_markdown_to_telegram_html("hello **world** end")
        assert "<b>world</b>" in result
        assert result.startswith("hello ")
        assert result.endswith(" end")


class TestItalic:
    def test_italic(self) -> None:
        assert convert_markdown_to_telegram_html("*italic*") == "<i>italic</i>"

    def test_italic_word_boundary(self) -> None:
        result = convert_markdown_to_telegram_html("this is *emphasized* text")
        assert "<i>emphasized</i>" in result


class TestStrikethrough:
    def test_strikethrough(self) -> None:
        assert convert_markdown_to_telegram_html("~~strike~~") == "<s>strike</s>"


class TestInlineCode:
    def test_inline_code(self) -> None:
        result = convert_markdown_to_telegram_html("`code`")
        assert result == "<code>code</code>"

    def test_no_markdown_conversion_inside_code(self) -> None:
        result = convert_markdown_to_telegram_html("`**not bold**`")
        assert result == "<code>**not bold**</code>"

    def test_html_chars_escaped_in_code(self) -> None:
        result = convert_markdown_to_telegram_html("`a < b & c > d`")
        assert "&lt;" in result
        assert "&amp;" in result
        assert "&gt;" in result


class TestFencedCodeBlock:
    def test_code_block(self) -> None:
        md = "```\nprint('hello')\n```"
        result = convert_markdown_to_telegram_html(md)
        assert "<pre><code>" in result
        assert "print(&#x27;hello&#x27;)" in result or "print('hello')" in result
        assert "</code></pre>" in result

    def test_code_block_with_language(self) -> None:
        md = "```python\nx = 1\n```"
        result = convert_markdown_to_telegram_html(md)
        assert "<pre><code>" in result
        assert "x = 1" in result

    def test_code_block_preserves_content(self) -> None:
        md = "```\n**not bold**\n*not italic*\n```"
        result = convert_markdown_to_telegram_html(md)
        assert "<b>" not in result
        assert "<i>" not in result
        assert "**not bold**" in result


class TestLinks:
    def test_link(self) -> None:
        result = convert_markdown_to_telegram_html("[Google](https://google.com)")
        assert result == '<a href="https://google.com">Google</a>'

    def test_link_in_text(self) -> None:
        result = convert_markdown_to_telegram_html(
            "visit [Google](https://google.com) now"
        )
        assert '<a href="https://google.com">Google</a>' in result


class TestHeadings:
    def test_h2(self) -> None:
        result = convert_markdown_to_telegram_html("## Title")
        assert result == "<b>Title</b>"

    def test_h1(self) -> None:
        result = convert_markdown_to_telegram_html("# Big Title")
        assert result == "<b>Big Title</b>"

    def test_heading_preserves_content(self) -> None:
        result = convert_markdown_to_telegram_html("### Hello World")
        assert result == "<b>Hello World</b>"


class TestBlockquotes:
    def test_single_line_quote(self) -> None:
        result = convert_markdown_to_telegram_html("> quoted text")
        assert "<blockquote>" in result
        assert "quoted text" in result
        assert "</blockquote>" in result

    def test_multiline_quote(self) -> None:
        md = "> line1\n> line2"
        result = convert_markdown_to_telegram_html(md)
        assert "<blockquote>" in result
        assert "</blockquote>" in result


class TestHtmlEscaping:
    def test_angle_brackets(self) -> None:
        result = convert_markdown_to_telegram_html("a < b > c")
        assert "&lt;" in result
        assert "&gt;" in result

    def test_ampersand(self) -> None:
        result = convert_markdown_to_telegram_html("a & b")
        assert "&amp;" in result

    def test_no_escaping_in_code_blocks(self) -> None:
        result = convert_markdown_to_telegram_html("```\nx < y\n```")
        assert "&lt;" in result  # Should be escaped inside code


class TestEdgeCases:
    def test_empty_string(self) -> None:
        assert convert_markdown_to_telegram_html("") == ""

    def test_plain_text(self) -> None:
        assert convert_markdown_to_telegram_html("just plain text") == "just plain text"

    def test_mixed_formatting(self) -> None:
        md = "Hello **world**, here is `code` and [link](https://example.com)"
        result = convert_markdown_to_telegram_html(md)
        assert "<b>world</b>" in result
        assert "<code>code</code>" in result
        assert '<a href="https://example.com">link</a>' in result

    def test_nested_bold_italic(self) -> None:
        result = convert_markdown_to_telegram_html("***bold italic***")
        # ***text*** = bold + italic: **text** + outer * → <i><b>text</b></i>
        assert "<b>" in result
        assert "<i>" in result


# ── split_html_message ────────────────────────────────────────────


class TestSplitHtmlMessage:
    def test_short_message(self) -> None:
        html = "<b>hello</b>"
        chunks = split_html_message(html)
        assert chunks == ["<b>hello</b>"]

    def test_empty_string(self) -> None:
        assert split_html_message("") == []

    def test_long_message_splits(self) -> None:
        # Create a message over 4096 chars
        html = "x" * 5000
        chunks = split_html_message(html)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= TELEGRAM_MSG_LIMIT

    def test_splits_at_paragraphs(self) -> None:
        para1 = "a" * 2000
        para2 = "b" * 2500
        html = f"{para1}\n\n{para2}"
        chunks = split_html_message(html)
        # Each paragraph fits in 4096, so should be 2 chunks
        assert len(chunks) == 2
        assert chunks[0] == para1
        assert chunks[1] == para2

    def test_single_huge_paragraph_hard_splits(self) -> None:
        html = "x" * 10000
        chunks = split_html_message(html)
        total_len = sum(len(c) for c in chunks)
        assert total_len == 10000
        for chunk in chunks:
            assert len(chunk) <= TELEGRAM_MSG_LIMIT
