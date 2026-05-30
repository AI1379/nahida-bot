"""v11 CQ code fallback parser.

Only used when the ``message`` array is empty or missing. The primary parse
path is always the structured segment array.
"""

from __future__ import annotations

import re
from typing import Any

from nahida_bot.channels.onebot.segment_models import OneBotSegment

# [CQ:type,k=v,k=v]
_CQ_PATTERN = re.compile(
    r"\[CQ:([a-zA-Z_][a-zA-Z0-9_]*)"  # type
    r"((?:,[^,\[\]]*=[^,\[\]]*)*)"  # key=value pairs
    r"\]"
)

# Text between CQ codes
_CQ_SPLIT = re.compile(r"(\[CQ:[^\]]+\])")


def parse_cq_code(text: str) -> list[OneBotSegment]:
    """Parse a CQ-code string into a list of segments.

    This is a **fallback** path for v11 ``raw_message``. Prefer the structured
    ``message`` array whenever available. Unrecognized CQ codes are preserved as
    text rather than dropped.
    """
    if not text:
        return []

    segments: list[OneBotSegment] = []
    parts = _CQ_SPLIT.split(text)

    for part in parts:
        if not part:
            continue
        m = _CQ_PATTERN.match(part)
        if m:
            seg_type = m.group(1)
            data = _parse_cq_params(m.group(2))
            segments.append(OneBotSegment(type=seg_type, data=data))
        else:
            if segments and segments[-1].type == "text":
                segments[-1].data["text"] = segments[-1].data.get("text", "") + part
            else:
                segments.append(OneBotSegment(type="text", data={"text": part}))

    return segments


def _parse_cq_params(raw: str) -> dict[str, Any]:
    """Parse CQ code key=value parameter string."""
    params: dict[str, Any] = {}
    if not raw:
        return params
    # Remove leading comma
    raw = raw.lstrip(",")
    # Simple split on comma not inside values (CQ values don't contain commas)
    for pair in raw.split(","):
        if "=" not in pair:
            continue
        key, _, value = pair.partition("=")
        key = key.strip()
        value = _cq_unescape(value.strip())
        if key:
            params[key] = value
    return params


def _cq_unescape(value: str) -> str:
    """Unescape CQ code special characters."""
    return (
        value.replace("&#44;", ",")
        .replace("&#91;", "[")
        .replace("&#93;", "]")
        .replace("&amp;", "&")
    )
