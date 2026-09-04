"""Markdown → Feishu rich-text (post) conversion.

Produces the ``post`` message content AST: paragraphs of elements with tags
``text`` (with optional ``style`` list), ``a``, ``at``, ``code_block``, and
``hr``. Covers the common LLM Markdown subset; unsupported syntax degrades to
plain readable text. The AST shape follows what the official SDK's own
markdown converter emits (structured mode), so rendering is deterministic
across Feishu client versions.
"""

from __future__ import annotations

import json
import re
from typing import Any

TextElement = dict[str, Any]
Paragraph = list[TextElement]

# Inline marker patterns (order matters: most specific first).
_INLINE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\*\*(.+?)\*\*"), "bold"),
    (re.compile(r"__(.+?)__"), "bold"),
    (re.compile(r"(?<!\*)\*(?!\*)(?!\s)([^*\n]+?)(?<!\s)\*(?!\*)"), "italic"),
    (re.compile(r"(?<!\w)_(?!_)(?!\s)([^_\n]+?)(?<!\s)_(?!_)(?!\w)"), "italic"),
    (re.compile(r"`([^`\n]+)`"), "code"),
    (re.compile(r"~~(.+?)~~"), "strikethrough"),
]
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_AT_RE = re.compile(r"<at\s+user_id=\"([^\"]+)\"[^>]*>([^<]*)</at>", re.IGNORECASE)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)")
_FENCE_OPEN_RE = re.compile(r"^```(\w*)\s*$")
_FENCE_CLOSE_RE = re.compile(r"^```\s*$")
_HR_RE = re.compile(r"^-{3,}$|^\*{3,}$|^_{3,}$")
_BULLET_RE = re.compile(r"^(\s*)[-*+]\s+(.*)")
_ORDERED_RE = re.compile(r"^(\s*)(\d+)([.)])\s+(.*)")
_TABLE_ROW_RE = re.compile(r"^\s*\|.+\|\s*$")

# Cheap pre-check: does this text contain any Markdown syntax worth turning
# into a post? Pure conversational text is sent as a plain text message.
_MD_HINT_RE = re.compile(
    r"\*\*|~~|`|^#{1,6}\s|^\s*[-*+]\s|^\s*\d+[.)]\s|^\s*>|\[[^\]]+\]\(|^(-{3,}|\*{3,})$",
    re.MULTILINE,
)


def looks_like_markdown(text: str) -> bool:
    """Whether the text contains Markdown worth rendering as rich text."""
    return bool(text) and bool(_MD_HINT_RE.search(text))


def markdown_to_post_content(markdown: str, *, title: str = "") -> str:
    """Convert Markdown into the JSON ``content`` string for a post message.

    The wire shape is ``{"post": {"zh_cn": {"title", "content"}}}``.
    """
    return json.dumps(
        {
            "post": {
                "zh_cn": {
                    "title": title,
                    "content": markdown_to_paragraphs(markdown),
                }
            }
        },
        ensure_ascii=False,
    )


def markdown_to_paragraphs(markdown: str) -> list[Paragraph]:
    """Convert Markdown into post paragraphs (the ``content`` 2-D array)."""
    paragraphs: list[Paragraph] = []
    lines = (markdown or "").splitlines()
    i = 0
    total = len(lines)
    buf: list[str] = []

    def flush_paragraph() -> None:
        if not buf:
            return
        text = "\n".join(buf).strip()
        buf.clear()
        if text:
            runs = _parse_inline_runs(text)
            if runs:
                paragraphs.append(runs)

    while i < total:
        line = lines[i]
        stripped = line.strip()

        if _HR_RE.fullmatch(stripped or ""):
            flush_paragraph()
            paragraphs.append([{"tag": "hr"}])
            i += 1
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            flush_paragraph()
            runs = _parse_inline_runs(heading.group(2).strip())
            for run in runs:
                if run.get("tag") == "text":
                    styles = run.setdefault("style", [])
                    if "bold" not in styles:
                        styles.append("bold")
            paragraphs.append(
                runs or [{"tag": "text", "text": heading.group(2).strip()}]
            )
            i += 1
            continue

        fence = _FENCE_OPEN_RE.match(stripped)
        if fence:
            flush_paragraph()
            language = (fence.group(1) or "TEXT").upper()
            code_lines: list[str] = []
            i += 1
            while i < total and not _FENCE_CLOSE_RE.match(lines[i].strip()):
                code_lines.append(lines[i])
                i += 1
            if i < total:
                i += 1  # skip the closing fence
            paragraphs.append(
                [
                    {
                        "tag": "code_block",
                        "language": language,
                        "text": "\n".join(code_lines),
                    }
                ]
            )
            continue

        if stripped.startswith(">"):
            flush_paragraph()
            quote_lines: list[str] = []
            while i < total and lines[i].lstrip().startswith(">"):
                quote_lines.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            quote_text = "\n".join(quote_lines).strip()
            if quote_text:
                paragraphs.append(
                    [{"tag": "text", "text": "│ "}] + _parse_inline_runs(quote_text)
                )
            continue

        if _BULLET_RE.match(line) or _ORDERED_RE.match(line):
            flush_paragraph()
            while i < total and (
                _BULLET_RE.match(lines[i]) or _ORDERED_RE.match(lines[i])
            ):
                bullet = _BULLET_RE.match(lines[i])
                if bullet:
                    item_text = "• " + bullet.group(2)
                else:
                    ordered = _ORDERED_RE.match(lines[i])
                    item_text = (
                        f"{ordered.group(2)}. {ordered.group(4)}"
                        if ordered
                        else lines[i]
                    )
                runs = _parse_inline_runs(item_text)
                if runs:
                    paragraphs.append(runs)
                i += 1
            continue

        if _TABLE_ROW_RE.match(line):
            flush_paragraph()
            table_lines: list[str] = []
            while i < total and _TABLE_ROW_RE.match(lines[i]):
                table_lines.append(lines[i])
                i += 1
            # Post has no table element; keep the raw rows as a text block so
            # the column structure stays readable.
            paragraphs.append([{"tag": "text", "text": "\n".join(table_lines)}])
            continue

        if not stripped:
            flush_paragraph()
            i += 1
            continue

        buf.append(line)
        i += 1

    flush_paragraph()
    if not paragraphs:
        text = (markdown or "").strip()
        return (
            [[{"tag": "text", "text": text}]]
            if text
            else [[{"tag": "text", "text": " "}]]
        )
    return paragraphs


def _parse_inline_runs(text: str) -> list[TextElement]:
    """Split one text block into styled runs, links, and plain text."""
    if not text:
        return []

    matches: list[tuple[int, int, TextElement]] = []
    for pattern, style in _INLINE_PATTERNS:
        for match in pattern.finditer(text):
            matches.append(
                (
                    match.start(),
                    match.end(),
                    {"tag": "text", "text": match.group(1), "style": [style]},
                )
            )
    for match in _LINK_RE.finditer(text):
        matches.append(
            (
                match.start(),
                match.end(),
                {"tag": "a", "text": match.group(1), "href": match.group(2)},
            )
        )
    for match in _AT_RE.finditer(text):
        user_id = match.group(1)
        name = match.group(2) or user_id
        element: TextElement = {"tag": "at", "user_id": user_id, "user_name": name}
        matches.append((match.start(), match.end(), element))

    # Keep disjoint matches only, leftmost-first.
    matches.sort(key=lambda item: (item[0], item[1]))
    filtered: list[tuple[int, int, TextElement]] = []
    cursor = 0
    for start, end, element in matches:
        if start < cursor:
            continue
        filtered.append((start, end, element))
        cursor = end

    runs: list[TextElement] = []
    pos = 0
    for start, end, element in filtered:
        if start > pos:
            runs.append({"tag": "text", "text": text[pos:start]})
        runs.append(element)
        pos = end
    if pos < len(text):
        runs.append({"tag": "text", "text": text[pos:]})

    # Merge adjacent plain text runs so paragraphs stay compact.
    merged: list[TextElement] = []
    for run in runs:
        if (
            run.get("tag") == "text"
            and not run.get("style")
            and merged
            and merged[-1].get("tag") == "text"
            and not merged[-1].get("style")
        ):
            merged[-1]["text"] += run["text"]
        else:
            merged.append(run)
    return [run for run in merged if run.get("text") or run.get("tag") in {"a", "at"}]


def split_markdown(markdown: str, *, limit: int) -> list[str]:
    """Split Markdown into chunks that each fit within *limit* characters.

    Prefers blank-line (paragraph) boundaries, then single newlines. Chunking
    happens on the raw Markdown so each chunk is converted independently.
    """
    if not markdown:
        return []
    if len(markdown) <= limit:
        return [markdown]

    chunks: list[str] = []
    paragraphs = markdown.split("\n\n")
    current: list[str] = []
    current_len = 0

    for paragraph in paragraphs:
        separator_len = 2 if current else 0
        if current_len + separator_len + len(paragraph) <= limit:
            current.append(paragraph)
            current_len += separator_len + len(paragraph)
            continue

        if current:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0

        if len(paragraph) > limit:
            for piece in _split_large_paragraph(paragraph, limit):
                chunks.append(piece)
            continue

        current.append(paragraph)
        current_len = len(paragraph)

    if current:
        chunks.append("\n\n".join(current))
    return chunks or [markdown[:limit]]


def _split_large_paragraph(paragraph: str, limit: int) -> list[str]:
    if "\n" in paragraph:
        pieces = paragraph.split("\n")
        separator = "\n"
    else:
        pieces = paragraph.split(" ")
        separator = " "
    # Hard-split unsplittable tokens first so accumulation can never exceed
    # the limit and no piece is dropped.
    bounded: list[str] = []
    for piece in pieces:
        if len(piece) <= limit:
            bounded.append(piece)
        else:
            bounded.extend(piece[i : i + limit] for i in range(0, len(piece), limit))
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for piece in bounded:
        separator_len = len(separator) if current else 0
        if current_len + separator_len + len(piece) <= limit:
            current.append(piece)
            current_len += separator_len + len(piece)
            continue
        if current:
            chunks.append(separator.join(current))
        current = [piece]
        current_len = len(piece)
    if current:
        chunks.append(separator.join(current))
    return chunks
