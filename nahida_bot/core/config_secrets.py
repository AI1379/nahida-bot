"""Sensitive config field knowledge, declared at the model layer.

Settings models annotate secret-bearing fields with
:data:`nahida_bot.core.config.SensitiveStr`; this module derives dotted path
patterns from the model tree so every consumer (WebUI redaction, CLI output)
shares one definition instead of each keeping a private regex.

A key-name regex remains as a fallback for untyped territory: ``Settings``
allows extra keys, channel sections (``telegram``, ``milky``, ``onebot``) and
plugin config blocks are not part of the typed model tree.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from nahida_bot.core.config import Settings

# Fallback heuristic for keys outside the typed model tree (channels,
# plugin config, ``extra="allow"`` sections).
SENSITIVE_KEY_PATTERN = re.compile(
    r"(api_key|token|secret|password|private_key|cookies?)", re.IGNORECASE
)


def _field_is_marked_sensitive(field: FieldInfo) -> bool:
    extra = field.json_schema_extra
    return isinstance(extra, dict) and bool(extra.get("nahida_sensitive"))


def _unwrap_models(annotation: Any) -> list[type[BaseModel]]:
    """Return BaseModel classes reachable through Optional/dict/list unions."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    models: list[type[BaseModel]] = []
    for arg in get_args(annotation):
        if isinstance(arg, type) and issubclass(arg, BaseModel):
            models.append(arg)
    return models


def _walk_model(
    model: type[BaseModel],
    prefix: str,
    patterns: set[str],
    seen: frozenset[type[BaseModel]],
) -> None:
    if model in seen:
        return
    seen = seen | {model}
    for name, field in model.model_fields.items():
        pattern = f"{prefix}.{name}" if prefix else name
        if _field_is_marked_sensitive(field):
            patterns.add(pattern)
        for sub in _unwrap_models(field.annotation):
            # dict[str, SubModel] fields introduce a wildcard key segment
            # (e.g. providers.<id>); plain Optional/nested models do not.
            origin = get_origin(field.annotation)
            is_mapping = origin is dict
            child_prefix = f"{pattern}.*" if is_mapping else pattern
            _walk_model(sub, child_prefix, patterns, seen)


@lru_cache(maxsize=8)
def sensitive_path_patterns() -> frozenset[str]:
    """Dotted path patterns of model-declared sensitive fields.

    ``*`` matches any single mapping-key segment, e.g.
    ``providers.*.api_key`` or ``webui.auth.admin_password_hash``.
    """
    patterns: set[str] = set()
    _walk_model(Settings, "", patterns, frozenset())
    return frozenset(patterns)


def _normalize_path(path: str) -> tuple[str, ...]:
    """Split a dotted path into segments, dropping ``[index]`` markers."""

    cleaned = re.sub(r"\[\d+\]", "", path)
    return tuple(segment for segment in cleaned.split(".") if segment)


def path_matches_pattern(path: str, pattern: str) -> bool:
    path_segments = _normalize_path(path)
    pattern_segments = tuple(pattern.split("."))
    if len(path_segments) != len(pattern_segments):
        return False
    return all(
        expected == "*" or expected == actual
        for expected, actual in zip(pattern_segments, path_segments)
    )


def is_sensitive_path(path: str, key: str) -> bool:
    """True when *path*/*key* is sensitive.

    Checks model-declared patterns first, then falls back to the key-name
    heuristic for untyped sections.
    """
    for pattern in sensitive_path_patterns():
        if path_matches_pattern(path, pattern):
            return True
    return bool(SENSITIVE_KEY_PATTERN.search(key))


def refresh_sensitive_path_patterns() -> None:
    """Clear the pattern cache (for tests that mutate model metadata)."""

    sensitive_path_patterns.cache_clear()


__all__ = [
    "SENSITIVE_KEY_PATTERN",
    "is_sensitive_path",
    "path_matches_pattern",
    "refresh_sensitive_path_patterns",
    "sensitive_path_patterns",
]
