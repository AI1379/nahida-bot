"""Comment-preserving targeted edits to YAML config files.

Built on ruamel.yaml round-trip mode so hand-maintained, comment-heavy files
(see a typical ``config-run.yaml``) survive programmatic edits: only the
targeted mapping entry changes, everything else — comments, ordering, quoting
— round-trips untouched. All writes are atomic (temp file + replace) and
optionally leave a timestamped sibling backup next to the original.

Intended as the single mutation primitive for config files across surfaces:
the auth/bootstrap CLI flows today, the Gateway config patch route when it
migrates off ``safe_load``/``safe_dump`` (which destroys comments).
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError


class YamlEditError(RuntimeError):
    """Raised when a YAML file cannot be parsed or written safely."""


def _yaml() -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def load_yaml_document(path: Path) -> CommentedMap:
    """Load *path* in round-trip mode; an empty result maps to an empty doc.

    Raises:
        YamlEditError: The file exists but is not valid YAML. The caller is
            expected to abort the edit rather than clobber the file.
    """
    if not path.exists():
        return CommentedMap()
    try:
        doc = _yaml().load(path.read_text(encoding="utf-8"))
    except YAMLError as exc:
        raise YamlEditError(f"Cannot parse {path}: {exc}") from exc
    if doc is None:
        return CommentedMap()
    if not isinstance(doc, CommentedMap):
        raise YamlEditError(
            f"Expected a YAML mapping at the top level of {path}, "
            f"got {type(doc).__name__}"
        )
    return doc


def upsert_path(doc: CommentedMap, segments: list[str], value: Any) -> None:
    """Set ``doc[a][b][c] = value`` in place, creating intermediate mappings.

    Existing intermediate values that are not mappings raise
    :class:`YamlEditError` instead of being silently replaced.
    """
    node: CommentedMap = doc
    for segment in segments[:-1]:
        child = node.get(segment)
        if child is None:
            child = CommentedMap()
            node[segment] = child
        elif not isinstance(child, CommentedMap):
            raise YamlEditError(
                f"Config path '{'.'.join(segments)}' crosses a non-mapping "
                f"value at '{segment}'"
            )
        node = child
    node[segments[-1]] = value


def document_to_text(doc: CommentedMap) -> str:
    """Serialize a round-trip document to text without touching any file."""

    sio = StringIO()
    _yaml().dump(doc, sio)
    return sio.getvalue()


def save_document(
    doc: CommentedMap,
    path: Path,
    *,
    backup: bool = True,
) -> str | None:
    """Serialize *doc* to *path* atomically, preserving comments.

    Returns the backup file path when one was written, else None.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path: str | None = None
    if backup and path.exists():
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        backup_file = path.with_name(f"{path.name}.bak.{timestamp}")
        shutil.copy2(path, backup_file)
        backup_path = str(backup_file)

    tmp_path = path.with_name(f"{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        _yaml().dump(doc, fh)
    tmp_path.replace(path)
    return backup_path


def upsert_entry(
    path: Path,
    section: str,
    key: str,
    entry: dict[str, Any],
    *,
    backup: bool = True,
) -> str | None:
    """Insert or replace ``<section>.<key>`` in the YAML file at *path*.

    The classic consumer is adding a ``providers.<id>`` entry. Returns the
    backup path when one was written, else None.
    """
    doc = load_yaml_document(path)
    existing = doc.get(section)
    if existing is not None and not isinstance(existing, CommentedMap):
        raise YamlEditError(f"Top-level key '{section}' in {path} is not a mapping")
    upsert_path(doc, [section, key], entry)
    return save_document(doc, path, backup=backup)
