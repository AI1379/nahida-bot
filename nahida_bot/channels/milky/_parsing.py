"""Small coercion helpers for Milky protocol dictionaries."""

from __future__ import annotations

from typing import Any


def as_mapping(value: object) -> dict[str, Any]:
    """Return value as a mapping, or an empty mapping for invalid data."""
    return value if isinstance(value, dict) else {}


def coerce_str(value: object, default: str = "") -> str:
    """Coerce a protocol value to string."""
    if value is None:
        return default
    return value if isinstance(value, str) else str(value)


def field_str(mapping: dict[str, Any], key: str, default: str = "") -> str:
    """Read and coerce a string field from a protocol mapping."""
    return coerce_str(mapping.get(key, default), default=default)


def coerce_int(value: object, default: int = 0) -> int:
    """Coerce a protocol value to int."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def field_int(mapping: dict[str, Any], key: str, default: int = 0) -> int:
    """Read and coerce an int field from a protocol mapping."""
    return coerce_int(mapping.get(key, default), default=default)


def coerce_bool(value: object, default: bool = False) -> bool:
    """Coerce common protocol bool representations."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return default


def field_bool(mapping: dict[str, Any], key: str, default: bool = False) -> bool:
    """Read and coerce a bool field from a protocol mapping."""
    return coerce_bool(mapping.get(key, default), default=default)


def coerce_str_list(value: object) -> list[str]:
    """Coerce a list-like protocol value to a string list."""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]
