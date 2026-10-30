"""Plain-text presentation shared by commands and runtime notifications.

System headings and field labels are English. User content, task results,
configured notices, and command stdout retain their original language.
"""

from collections.abc import Iterable


def format_text_section(title: str, lines: Iterable[str], *, icon: str = "") -> str:
    """Group readable fields under one lightly marked heading."""
    heading = f"{icon} {title}" if icon else title
    return "\n".join((heading, *lines))


def compact_label(value: str, *, limit: int = 80) -> str:
    """Keep dynamic labels on one bounded line."""
    text = " ".join(value.split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def format_system_text(
    area: str,
    summary: str,
    *,
    fields: Iterable[tuple[str, str]] = (),
    body: str = "",
) -> str:
    """Render a predictable heading, optional labeled fields, and content."""
    lines = [f"[{area}] {compact_label(summary)}"]
    lines.extend(
        f"{label}: {compact_label(value, limit=240)}" for label, value in fields
    )
    if body.strip():
        lines.extend(("", body.strip()))
    return "\n".join(lines)
