"""Read-only file-content search over configured roots (search_files).

Gives every sender — including non-admins — a grep-like lookup over
owner-declared read-only directories (e.g. a cloned reference repo) without
opening the shell ``exec`` tool. Pure-Python traversal: no subprocess, no
shell string building, so there is no command-injection surface. Only roots
listed in the plugin config are ever searched; the optional ``path`` argument
must resolve inside one of those roots.
"""

from __future__ import annotations

import asyncio
import fnmatch
import os
from pathlib import Path
from typing import Any

import structlog

from nahida_bot.plugins.tooling import PluginToolDefinition
from nahida_bot_sdk.api import BotAPI


_logger = structlog.get_logger(__name__)

_MAX_TOOL_OUTPUT = 50_000
_MAX_MATCHES = 200
_MAX_MATCHES_PER_FILE = 20
_MAX_LINE_CHARS = 500
_MAX_FILE_BYTES = 2_000_000
_PRUNED_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", ".idea"})

_SEARCH_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "Case-insensitive substring to find in file contents. Keep it "
                "short and distinctive."
            ),
        },
        "path": {
            "type": "string",
            "description": (
                "Optional root-relative subdirectory or file to narrow the "
                "search. Absolute paths and paths escaping a configured root "
                "are rejected."
            ),
        },
        "glob": {
            "type": "string",
            "description": (
                "Optional filename filter, e.g. '*.md'. Default: all files "
                "(binary files are skipped automatically)."
            ),
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}


class FileSearchTools:
    """Define and execute content search over configured read-only roots."""

    def __init__(self, api: BotAPI, roots: list[str] | None) -> None:
        self._api = api
        self._roots = tuple(
            root
            for root in (Path(str(raw)).expanduser().resolve() for raw in (roots or []))
            if root.is_dir()
        )

    @property
    def configured(self) -> bool:
        return bool(self._roots)

    def definitions(self) -> tuple[PluginToolDefinition, ...]:
        """Return the file search tool exposed to the model (empty when unset)."""
        if not self._roots:
            return ()
        roots_label = ", ".join(str(root) for root in self._roots)
        return (
            PluginToolDefinition(
                name="search_files",
                description=(
                    "Case-insensitive substring search over file contents in "
                    "configured read-only reference directories (no shell, no "
                    "writes). Available to every sender. Returns file:line "
                    "matches so you can quote sources precisely. Configured "
                    f"roots: {roots_label}. Use find/grep-like narrow queries; "
                    "narrow with path (root-relative) or glob ('*.md') when "
                    "there are too many hits."
                ),
                parameters=_SEARCH_PARAMETERS,
                handler=self.search,
            ),
        )

    async def search(self, query: str, path: str = "", glob: str = "") -> str:
        """Search configured roots for a case-insensitive substring."""
        needle = (query or "").strip()
        if not needle:
            return "Error: empty search query."
        if not self._roots:
            return (
                "search_files is not configured: no search roots are set in "
                "the builtin plugin config (file_search_roots)."
            )
        targets, error = self._resolve_targets(path)
        if error:
            return error
        needle_lower = needle.casefold()
        glob_pattern = (glob or "").strip()
        try:
            blocks, match_count, file_count = await asyncio.to_thread(
                self._search_sync, targets, needle_lower, glob_pattern
            )
        except OSError as exc:
            _logger.warning("tool.search_files_failed", error=str(exc))
            return f"Error searching files: {exc}"
        if not match_count:
            searched = ", ".join(str(target) for target in targets)
            return f"No matches for {query!r} under: {searched}"
        header = (
            f"Found {match_count} match(es) for {query!r} in {file_count} "
            f"file(s) (case-insensitive):"
        )
        if match_count >= _MAX_MATCHES:
            header += f" [capped at {_MAX_MATCHES} matches — narrow with path/glob]"
        output = "\n".join([header, *blocks])
        if len(output) > _MAX_TOOL_OUTPUT:
            output = output[:_MAX_TOOL_OUTPUT] + "\n[output truncated]"
        return output

    def _resolve_targets(self, path: str) -> tuple[tuple[Path, ...], str]:
        """Resolve the optional ``path`` argument against the configured roots.

        Returns an error string when the argument is absolute or escapes every
        root; otherwise the narrowed target directories/files.
        """
        raw = (path or "").strip()
        if not raw:
            return self._roots, ""
        candidate = Path(raw)
        if candidate.is_absolute() or ".." in candidate.parts:
            return (), (
                f"Error: path must be relative to a configured root and must "
                f"not contain '..'. Got: {raw!r}."
            )
        targets: list[Path] = []
        for root in self._roots:
            resolved = (root / candidate).resolve()
            if resolved == root or root in resolved.parents:
                if resolved.exists():
                    targets.append(resolved)
        if not targets:
            roots_label = ", ".join(str(root) for root in self._roots)
            return (), (
                f"Error: {raw!r} does not exist inside any configured root "
                f"({roots_label})."
            )
        return tuple(targets), ""

    def _search_sync(
        self, targets: tuple[Path, ...], needle_lower: str, glob_pattern: str
    ) -> tuple[list[str], int, int]:
        """Walk targets and collect matches (runs in a worker thread)."""
        blocks: list[str] = []
        match_count = 0
        file_count = 0
        for target in targets:
            files = self._iter_files(target, glob_pattern)
            for file_path in files:
                if match_count >= _MAX_MATCHES:
                    return blocks, match_count, file_count
                try:
                    if file_path.stat().st_size > _MAX_FILE_BYTES:
                        continue
                    text = file_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if "\x00" in text[:4096]:
                    continue  # binary
                hits = self._match_lines(text, needle_lower)
                if not hits:
                    continue
                file_count += 1
                if file_path == target:
                    rel = file_path.name
                else:
                    try:
                        rel = file_path.relative_to(target)
                    except ValueError:
                        rel = file_path
                lines = [f"- {rel}"]
                for lineno, line_text in hits:
                    lines.append(f"  L{lineno}: {line_text}")
                blocks.append("\n".join(lines))
                match_count += len(hits)
        return blocks, match_count, file_count

    @staticmethod
    def _iter_files(target: Path, glob_pattern: str):
        """Yield candidate files under ``target``, pruning vendor directories."""
        if target.is_file():
            yield target
            return
        for dirpath, dirnames, filenames in os.walk(target, followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in _PRUNED_DIRS]
            for filename in filenames:
                if glob_pattern and not fnmatch.fnmatch(filename, glob_pattern):
                    continue
                yield Path(dirpath) / filename

    @staticmethod
    def _match_lines(text: str, needle_lower: str) -> list[tuple[int, str]]:
        """Return (1-based line number, stripped line) for matching lines."""
        hits: list[tuple[int, str]] = []
        for lineno, line in enumerate(text.splitlines(), start=1):
            if needle_lower in line.casefold():
                hits.append((lineno, line.strip()[:_MAX_LINE_CHARS]))
                if len(hits) >= _MAX_MATCHES_PER_FILE:
                    break  # per-file cap keeps one dense file from flooding
        return hits
