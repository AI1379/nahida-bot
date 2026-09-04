"""Coercion helpers for loosely-typed Feishu event payloads."""

from __future__ import annotations

from typing import Any


def as_mapping(value: object) -> dict[str, Any]:
    """Return *value* as a dict; empty dict for non-mapping input."""
    return value if isinstance(value, dict) else {}


def coerce_str(value: object, default: str = "") -> str:
    """Coerce a raw event value to a stripped string."""
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip()
    return str(value)


def coerce_int(value: object, default: int = 0) -> int:
    """Coerce a raw event value to an int, tolerating numeric strings."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return default
        try:
            return int(float(stripped))
        except ValueError:
            return default
    return default


def coerce_bool(value: object, default: bool = False) -> bool:
    """Coerce a raw event value to a bool."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
