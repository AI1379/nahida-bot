"""Log entry normalization and low-noise sensitive value redaction."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from nahida_bot.gateway.schemas import LogEntry

REDACTED = "***"

_KNOWN_LOG_KEYS = {"timestamp", "level", "logger", "event"}

_SAFE_FIELD_NAMES = {
    "input_tokens",
    "output_tokens",
    "cached_tokens",
    "reasoning_tokens",
    "cache_creation_tokens",
    "token_usage",
    "session_key",
    "session_key_kind",
}

_SENSITIVE_FIELD_NAMES = {
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "auth_token",
    "api_token",
    "bot_token",
    "secret",
    "client_secret",
    "password",
    "passwd",
    "private_key",
    "authorization",
    "proxy_authorization",
    "x_api_key",
    "cookie",
    "set_cookie",
}

_VALUE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        REDACTED,
    ),
    (re.compile(r"(?i)\b(Bearer)\s+([A-Za-z0-9._~+/=-]{8,})"), rf"\1 {REDACTED}"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), REDACTED),
    (re.compile(r"\bgh[opsu]_[A-Za-z0-9_]{20,}\b"), REDACTED),
    (re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b"), REDACTED),
)


def to_log_entry(raw: Mapping[str, Any]) -> LogEntry:
    """Return a WebUI log entry with a stable shape and conservative redaction."""
    fields = {
        str(key): redact_log_value(value, field_name=str(key))
        for key, value in raw.items()
        if str(key) not in _KNOWN_LOG_KEYS
    }
    return LogEntry(
        timestamp=str(raw.get("timestamp", "")),
        level=str(raw.get("level", "")).lower(),
        logger=str(raw.get("logger", "")),
        event=str(redact_log_value(raw.get("event", ""), field_name="event")),
        fields=fields,
    )


def redact_log_value(value: Any, *, field_name: str | None = None) -> Any:
    """Redact obvious secrets while avoiding broad substring guesses."""
    if field_name is not None and _is_sensitive_field(field_name):
        return REDACTED

    if isinstance(value, str):
        return _redact_string(value)

    if isinstance(value, Mapping):
        return {
            str(key): redact_log_value(item, field_name=str(key))
            for key, item in value.items()
        }

    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [redact_log_value(item) for item in value]

    return value


def _is_sensitive_field(field_name: str) -> bool:
    normalized = _normalize_field_name(field_name)
    if normalized in _SAFE_FIELD_NAMES:
        return False
    return normalized in _SENSITIVE_FIELD_NAMES


def _normalize_field_name(field_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", field_name.lower()).strip("_")


def _redact_string(value: str) -> str:
    redacted = value
    for pattern, replacement in _VALUE_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted
