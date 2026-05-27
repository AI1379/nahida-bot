"""Log viewer endpoint."""

from __future__ import annotations

import json

from fastapi import APIRouter, Query

from nahida_bot.core.logging import get_log_capture
from nahida_bot.gateway.schemas import LogsResponse
from nahida_bot.gateway.services.log_redaction import to_log_entry

router = APIRouter()

_LEVEL_ORDER = {
    "trace": 5,
    "debug": 10,
    "info": 20,
    "warning": 30,
    "error": 40,
    "critical": 50,
}


@router.get("/api/logs", response_model=LogsResponse)
async def get_logs(
    level: str = Query(""),
    logger: str = Query(""),
    search: str = Query(""),
    limit: int = Query(200, ge=1, le=2000),
) -> LogsResponse:
    capture = get_log_capture()
    if capture is None:
        return LogsResponse(entries=[])

    entries = capture.get_entries()

    if level and level.lower() != "all":
        min_level = _LEVEL_ORDER.get(level.lower(), 0)
        entries = [
            e
            for e in entries
            if _LEVEL_ORDER.get(str(e.get("level", "")).lower(), 0) >= min_level
        ]

    if logger:
        logger_lower = logger.lower()
        entries = [
            e for e in entries if logger_lower in str(e.get("logger", "")).lower()
        ]

    if search:
        search_lower = search.lower()
        entries = [
            e for e in entries if search_lower in json.dumps(e, default=str).lower()
        ]

    entries = entries[-limit:]
    return LogsResponse(entries=[to_log_entry(e) for e in entries])
