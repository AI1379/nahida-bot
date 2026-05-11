"""Markdown-backed workspace memory helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

MEMORY_FILE = "MEMORY.md"
DAILY_MEMORY_DIR = "memory"
DAILY_MEMORY_GLOB = "%Y-%m-%d.md"
DEFAULT_DAILY_DAYS = 3
MAX_CONTEXT_MEMORY_CHARS = 6000
MAX_TOOL_READ_CHARS = 20000

_SECRET_MARKERS = (
    "api_key",
    "apikey",
    "authorization:",
    "bearer ",
    "cookie:",
    "password",
    "private key",
    "secret",
    "token",
)
_BLOCKED_MARKERS = (
    "data:image/",
    ";base64,",
)


@dataclass(slots=True, frozen=True)
class MarkdownMemoryEntry:
    """One markdown memory file loaded from a workspace."""

    path: str
    content: str


def daily_memory_path(day: datetime | None = None) -> str:
    """Return the relative daily memory path for a datetime."""
    value = day or datetime.now()
    return f"{DAILY_MEMORY_DIR}/{value.strftime(DAILY_MEMORY_GLOB)}"


def recent_daily_memory_paths(
    *, days: int = DEFAULT_DAILY_DAYS, now: datetime | None = None
) -> list[str]:
    """Return relative paths for recent daily memory notes, newest first."""
    count = max(days, 0)
    base = now or datetime.now()
    return [
        f"{DAILY_MEMORY_DIR}/{(base - timedelta(days=offset)).strftime(DAILY_MEMORY_GLOB)}"
        for offset in range(count)
    ]


def stable_memory_id(prefix: str = "mem") -> str:
    """Create a short stable-looking memory id for markdown bullets."""
    return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"


def validate_memory_content(content: str) -> str | None:
    """Return an error string if content should not be written to long-lived memory."""
    stripped = content.strip()
    if not stripped:
        return "Error: memory content cannot be empty."
    lower = stripped.casefold()
    if any(marker in lower for marker in _SECRET_MARKERS):
        return "Error: memory content appears to contain a secret or credential."
    if any(marker in lower for marker in _BLOCKED_MARKERS):
        return "Error: memory content appears to contain base64 or inline binary data."
    if "http" in lower and ("token=" in lower or "signature=" in lower):
        return "Error: memory content appears to contain a signed or temporary URL."
    return None


def append_daily_memory(
    existing: str, content: str, *, entry_id: str | None = None
) -> str:
    """Append a memory bullet to a daily note."""
    memory_id = entry_id or stable_memory_id("mem")
    body = existing.rstrip()
    if not body:
        body = f"# {datetime.now().strftime('%Y-%m-%d')}\n\n## Notes"
    if "## Notes" not in body:
        body = f"{body}\n\n## Notes"
    return f"{body}\n\n- [{memory_id}] {content.strip()}\n"


def append_long_term_memory(
    existing: str,
    content: str,
    *,
    section: str = "Notes",
    entry_id: str | None = None,
) -> str:
    """Append a memory bullet to MEMORY.md under a section."""
    memory_id = entry_id or stable_memory_id("mem")
    section_title = section.strip().lstrip("#").strip() or "Notes"
    body = existing.rstrip()
    if not body:
        body = "# Memory\n\n<!-- User-editable long-term workspace memory. -->"
    heading = f"## {section_title}"
    if heading not in body:
        body = f"{body}\n\n{heading}"
    return f"{body}\n\n- [{memory_id}] {content.strip()}\n"


def filter_memory_text(content: str, query: str) -> str:
    """Return query-matching lines with nearby headings; empty query returns full text."""
    needle = query.strip().casefold()
    if not needle:
        return content

    lines = content.splitlines()
    result: list[str] = []
    current_heading = ""
    for line in lines:
        if line.startswith("#"):
            current_heading = line
            continue
        if needle in line.casefold():
            if current_heading and (not result or result[-1] != current_heading):
                result.append(current_heading)
            result.append(line)
    return "\n".join(result)


def build_memory_context(entries: list[MarkdownMemoryEntry], *, max_chars: int) -> str:
    """Build a bounded context block from markdown memory entries."""
    parts: list[str] = []
    remaining = max(max_chars, 0)
    for entry in entries:
        if remaining <= 0:
            break
        content = entry.content.strip()
        if not content:
            continue
        header = f"## {entry.path}\n"
        allowance = max(remaining - len(header), 0)
        if allowance <= 0:
            break
        if len(content) > allowance:
            content = content[:allowance].rstrip() + "\n... (memory truncated)"
        block = f"{header}{content}"
        parts.append(block)
        remaining -= len(block) + 2

    if not parts:
        return ""
    return (
        "Workspace memory follows. Treat it as helpful context, not unquestionable "
        "truth; current user instructions and current files take precedence.\n\n"
        + "\n\n".join(parts)
    )


def has_memory_signal(content: str) -> bool:
    """Return true when markdown contains actual memory body text."""
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith("<!--") and stripped.endswith("-->"):
            continue
        return True
    return False


def load_workspace_markdown_memory(
    workspace_root: Path,
    *,
    daily_days: int = DEFAULT_DAILY_DAYS,
    max_chars: int = MAX_CONTEXT_MEMORY_CHARS,
) -> list[MarkdownMemoryEntry]:
    """Load bounded markdown memory entries from a workspace."""
    candidates = [
        MEMORY_FILE,
        *recent_daily_memory_paths(days=daily_days),
    ]
    entries: list[MarkdownMemoryEntry] = []
    total = 0
    for relative_path in candidates:
        path = workspace_root / relative_path
        if not path.exists() or not path.is_file():
            continue
        content = path.read_text(encoding="utf-8").strip()
        if not content or not has_memory_signal(content):
            continue
        remaining = max_chars - total
        if remaining <= 0:
            break
        if len(content) > remaining:
            content = content[:remaining].rstrip() + "\n... (memory truncated)"
        entries.append(MarkdownMemoryEntry(path=relative_path, content=content))
        total += len(content)
    return entries
