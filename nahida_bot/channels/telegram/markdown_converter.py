"""Markdown → Telegram HTML converter.

Converts standard Markdown emitted by LLMs into Telegram-compatible HTML.
Telegram supports a limited subset of HTML tags: <b>, <i>, <u>, <s>, <code>,
<pre>, <a href>, <blockquote>, <tg-spoiler>.
"""

from __future__ import annotations

import re
from html import escape as html_escape

# Telegram message character limit
TELEGRAM_MSG_LIMIT = 4096

# Sentinel pattern used to protect code blocks / inline code during conversion.
_PLACEHOLDER_RE = re.compile(r"\x00PH(\d+)\x00")


def convert_markdown_to_telegram_html(text: str) -> str:
    """Convert a Markdown string to Telegram-compatible HTML."""
    if not text:
        return ""

    placeholders: list[str] = []

    # Phase 1: Extract fenced code blocks (``` ... ```)
    text = _extract_fenced_code(text, placeholders)

    # Phase 2: Extract inline code (` ... `)
    text = _extract_inline_code(text, placeholders)

    # Phase 3: HTML-escape remaining text
    text = html_escape(text, quote=False)

    # Phase 4: Convert block-level Markdown
    text = _convert_blockquotes(text)
    text = _convert_headings(text)

    # Phase 5: Convert inline Markdown
    text = _convert_links(text)
    text = _convert_bold(text)
    text = _convert_italic(text)
    text = _convert_strikethrough(text)

    # Phase 6: Reinsert code placeholders
    text = _reinsert_placeholders(text, placeholders)

    return text


def split_html_message(html: str, limit: int = TELEGRAM_MSG_LIMIT) -> list[str]:
    """Split HTML text into chunks that each fit within *limit* characters.

    Prefers splitting at paragraph boundaries (``\\n\\n``).  Falls back to
    splitting at ``\\n`` or spaces when a single paragraph exceeds the limit.
    """
    if not html:
        return []

    if len(html) <= limit:
        return [html]

    chunks: list[str] = []

    # Try paragraph splits first
    paragraphs = html.split("\n\n")
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para)
        separator_len = 2 if current else 0  # \n\n between paras

        if current_len + separator_len + para_len <= limit:
            current.append(para)
            current_len += separator_len + para_len
        else:
            if current:
                chunks.append("\n\n".join(current))
                current = []
                current_len = 0

            # Single paragraph too large — split further
            if para_len > limit:
                sub_chunks = _split_large_segment(para, limit)
                chunks.extend(sub_chunks[:-1])
                # Keep last sub-chunk as start of new current
                current = [sub_chunks[-1]]
                current_len = len(sub_chunks[-1])
            else:
                current = [para]
                current_len = para_len

    if current:
        chunks.append("\n\n".join(current))

    return chunks if chunks else [html[:limit]]


# ── Internal helpers ─────────────────────────────────────────


def _placeholder(store: list[str], value: str) -> str:
    idx = len(store)
    store.append(value)
    return f"\x00PH{idx}\x00"


def _extract_fenced_code(text: str, placeholders: list[str]) -> str:
    """Replace fenced code blocks (``` ... ```) with placeholders."""
    pattern = re.compile(r"```[\w]*\n?(.*?)```", re.DOTALL)

    def _replace(m: re.Match[str]) -> str:
        code = html_escape(m.group(1).rstrip("\n"), quote=False)
        return _placeholder(placeholders, f"<pre><code>{code}</code></pre>")

    return pattern.sub(_replace, text)


def _extract_inline_code(text: str, placeholders: list[str]) -> str:
    """Replace inline code (` ... `) with placeholders."""
    pattern = re.compile(r"`([^`\n]+)`")

    def _replace(m: re.Match[str]) -> str:
        code = html_escape(m.group(1), quote=False)
        return _placeholder(placeholders, f"<code>{code}</code>")

    return pattern.sub(_replace, text)


def _convert_bold(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)


def _convert_italic(text: str) -> str:
    # Bold (**..**) is already converted to <b> tags, so remaining single *
    # are italic markers.  Avoid matching across tag boundaries.
    return re.sub(r"\*([^*\n]+?)\*", r"<i>\1</i>", text)


def _convert_strikethrough(text: str) -> str:
    return re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)


def _convert_links(text: str) -> str:
    def _replace(m: re.Match[str]) -> str:
        link_text = m.group(1)
        url = m.group(2)
        return f'<a href="{url}">{link_text}</a>'

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _replace, text)


def _convert_headings(text: str) -> str:
    """Convert ## Heading lines to <b>Heading</b>."""

    def _replace(m: re.Match[str]) -> str:
        content = m.group(1).strip()
        return f"<b>{content}</b>"

    return re.sub(r"^#{1,6}\s+(.+)$", _replace, text, flags=re.MULTILINE)


def _convert_blockquotes(text: str) -> str:
    """Convert > quote lines to <blockquote>...</blockquote>."""
    lines = text.split("\n")
    result: list[str] = []
    in_quote = False

    for line in lines:
        if line.startswith("&gt; "):
            quote_content = line[5:]
            if not in_quote:
                result.append("<blockquote>")
                in_quote = True
            result.append(quote_content)
        else:
            if in_quote:
                result.append("</blockquote>")
                in_quote = False
            result.append(line)

    if in_quote:
        result.append("</blockquote>")

    return "\n".join(result)


def _reinsert_placeholders(text: str, placeholders: list[str]) -> str:
    """Restore placeholder tokens with their original HTML content."""

    def _replace(m: re.Match[str]) -> str:
        idx = int(m.group(1))
        if 0 <= idx < len(placeholders):
            return placeholders[idx]
        return m.group(0)

    return _PLACEHOLDER_RE.sub(_replace, text)


def _split_large_segment(text: str, limit: int) -> list[str]:
    """Split a single large segment that exceeds *limit*."""
    # Try line splits
    if "\n" in text:
        lines = text.split("\n")
        return _accumulate_chunks(lines, "\n", limit)

    # Fall back to space splits
    if " " in text:
        words = text.split(" ")
        return _accumulate_chunks(words, " ", limit)

    # Hard split
    return [text[i : i + limit] for i in range(0, len(text), limit)]


def _accumulate_chunks(pieces: list[str], sep: str, limit: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for piece in pieces:
        sep_len = len(sep) if current else 0
        if current_len + sep_len + len(piece) <= limit:
            current.append(piece)
            current_len += sep_len + len(piece)
        else:
            if current:
                chunks.append(sep.join(current))
            current = [piece]
            current_len = len(piece)

    if current:
        chunks.append(sep.join(current))

    return chunks
