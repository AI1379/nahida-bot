"""Structured audit log for gateway mutations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def audit(
    action: str,
    *,
    detail: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    """Write a structured audit log entry for a gateway mutation."""
    event: dict[str, Any] = {
        "action": action,
        "ts": datetime.now(UTC).isoformat(),
    }
    if detail:
        event["detail"] = detail
    if extra:
        event.update(extra)
    logger.info("audit." + action, **event)
