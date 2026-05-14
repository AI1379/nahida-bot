"""Runtime settings persisted as session metadata."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

RUNTIME_META_KEY = "runtime"
REASONING_EFFORTS = frozenset({"low", "medium", "high", "max"})


@dataclass(slots=True, frozen=True)
class ReasoningRuntimeSettings:
    """Per-session runtime reasoning settings."""

    show: bool | None = None
    effort: str | None = None


@dataclass(slots=True, frozen=True)
class RuntimeSettings:
    """Runtime settings active for one request/session."""

    reasoning: ReasoningRuntimeSettings = field(
        default_factory=ReasoningRuntimeSettings
    )


current_runtime_settings: ContextVar[RuntimeSettings] = ContextVar(
    "current_runtime_settings",
    default=RuntimeSettings(),
)


def runtime_meta_from_session_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Return the raw runtime metadata dict from session metadata."""
    if not isinstance(meta, dict):
        return {}
    raw = meta.get(RUNTIME_META_KEY)
    return _copy_dict(raw) if isinstance(raw, dict) else {}


def runtime_settings_from_meta(meta: dict[str, Any] | None) -> RuntimeSettings:
    """Parse session metadata into typed runtime settings."""
    runtime = runtime_meta_from_session_meta(meta)
    reasoning_raw = runtime.get("reasoning")
    reasoning = reasoning_raw if isinstance(reasoning_raw, dict) else {}

    show_raw = reasoning.get("show")
    show = show_raw if isinstance(show_raw, bool) else None

    effort_raw = reasoning.get("effort")
    effort = _normalize_reasoning_effort(effort_raw)

    return RuntimeSettings(
        reasoning=ReasoningRuntimeSettings(
            show=show,
            effort=effort,
        )
    )


def merge_runtime_meta(
    existing: dict[str, Any] | None,
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Deep-merge runtime metadata updates.

    ``None`` update values delete the corresponding key, which lets commands
    reset a setting back to the config/provider default.
    """
    base = _copy_dict(existing) if isinstance(existing, dict) else {}
    return _deep_merge(base, updates)


def _normalize_reasoning_effort(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized not in REASONING_EFFORTS:
        return None
    return normalized


def _deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in updates.items():
        if value is None:
            merged.pop(key, None)
            continue
        if isinstance(value, dict):
            current = merged.get(key)
            child = _deep_merge(
                _copy_dict(current) if isinstance(current, dict) else {},
                value,
            )
            if child:
                merged[key] = child
            else:
                merged.pop(key, None)
            continue
        merged[key] = value
    return merged


def _copy_dict(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    copied: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, dict):
            copied[key] = _copy_dict(item)
        else:
            copied[key] = item
    return copied
