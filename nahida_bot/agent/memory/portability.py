"""Lightweight portability policy for durable memory metadata.

``scope`` says where a memory primarily belongs. ``sensitivity`` says how
harmful disclosure may be. ``portable`` is the small missing bit between them:
whether a public item may participate in soft-scope recall outside its primary
scope. It deliberately lives in ``metadata_json`` so the policy can ship
without a schema migration.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def normalize_portable(value: object, *, default: bool = True) -> bool:
    """Normalize a metadata value to a strict portability boolean.

    Missing values preserve the historical portable-by-default behavior.
    Explicit false-like values are accepted for compatibility with JSON/tool
    callers; unrecognized values use ``default`` rather than silently changing
    an item's recall boundary.
    """

    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    text = str(value).strip().casefold()
    if text in {"false", "no", "off", "0"}:
        return False
    if text in {"true", "yes", "on", "1"}:
        return True
    return default


def metadata_is_portable(metadata: object) -> bool:
    """Return whether one metadata mapping allows cross-scope recall."""

    if not isinstance(metadata, Mapping):
        return True
    return normalize_portable(metadata.get("portable"), default=True)


def item_is_portable(item: Any) -> bool:
    """Read the portability policy from a memory-like object's metadata."""

    return metadata_is_portable(getattr(item, "metadata", None))
