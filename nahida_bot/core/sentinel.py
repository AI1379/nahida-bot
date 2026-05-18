"""Sentinel token detection for reply suppression.

Provides multi-layer detection of sentinel tokens (NO_REPLY, HEARTBEAT_OK)
that allow the LLM to signal reply suppression through its text output.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

SENTINEL_NO_REPLY = "NO_REPLY"
SENTINEL_HEARTBEAT_OK = "HEARTBEAT_OK"
_VALID_SENTINELS = frozenset({SENTINEL_NO_REPLY, SENTINEL_HEARTBEAT_OK})

# Matches a sentinel token on its own line at the end of text.
_TRIPPING_RE = re.compile(
    r"\s*\n\s*(" + "|".join(re.escape(s) for s in _VALID_SENTINELS) + r")\s*$",
    re.IGNORECASE,
)


@dataclass(slots=True, frozen=True)
class SentinelResult:
    """Result of sentinel detection on a text string."""

    action: str | None  # "NO_REPLY" | "HEARTBEAT_OK" | None
    text: str  # remaining text after stripping sentinel


def detect_sentinel(text: str) -> SentinelResult:
    """Detect a sentinel token in *text*.

    Three detection layers, tried in order:
    1. Exact match  – the entire trimmed text is a sentinel token.
    2. JSON envelope – ``{"action": "NO_REPLY"}`` (or HEARTBEAT_OK).
    3. Trailing strip – sentinel token on its own line at the end.
    """
    if not text or not text.strip():
        return SentinelResult(action=None, text=text)

    # Layer 1: exact match
    action = _match_exact(text)
    if action is not None:
        return SentinelResult(action=action, text="")

    # Layer 2: JSON envelope
    action = _match_json_envelope(text)
    if action is not None:
        return SentinelResult(action=action, text="")

    # Layer 3: trailing strip
    action, remaining = _match_trailing(text)
    if action is not None:
        return SentinelResult(action=action, text=remaining)

    return SentinelResult(action=None, text=text)


def _match_exact(text: str) -> str | None:
    stripped = text.strip().upper()
    if stripped in _VALID_SENTINELS:
        return stripped
    return None


def _match_json_envelope(text: str) -> str | None:
    stripped = text.strip()
    if not stripped.startswith("{"):
        return None
    try:
        obj = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    action_val = obj.get("action")
    if not isinstance(action_val, str):
        return None
    if action_val.upper() in _VALID_SENTINELS:
        return action_val.upper()
    return None


def _match_trailing(text: str) -> tuple[str | None, str]:
    m = _TRIPPING_RE.search(text)
    if m is None:
        return None, text
    action = m.group(1).upper()
    remaining = text[: m.start()].rstrip()
    return action, remaining
