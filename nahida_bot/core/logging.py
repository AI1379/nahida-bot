"""Logging setup helpers for Nahida Bot."""

from __future__ import annotations

import logging
from typing import Any, cast

import structlog

_configured = False
TRACE_LEVEL = 5
logging.addLevelName(TRACE_LEVEL, "TRACE")


def _stdlib_trace(
    self: logging.Logger, message: object, *args: object, **kwargs: Any
) -> None:
    if self.isEnabledFor(TRACE_LEVEL):
        self._log(TRACE_LEVEL, message, args, **cast(dict[str, Any], kwargs))


if not hasattr(logging.Logger, "trace"):
    logging.Logger.trace = _stdlib_trace  # type: ignore[attr-defined]


def log_trace(logger: object, event: str, **kwargs: object) -> None:
    """Emit a structlog event at the custom TRACE level when supported."""
    log = getattr(logger, "log", None)
    if callable(log):
        try:
            log(TRACE_LEVEL, event, **kwargs)
            return
        except Exception:
            pass
    debug = getattr(logger, "debug", None)
    if callable(debug):
        debug(event, trace_fallback=True, **kwargs)


def configure_logging(
    *,
    debug: bool,
    log_level: str = "INFO",
    log_json: bool | None = None,
) -> None:
    """Configure stdlib logging + structlog processors once per process."""
    global _configured
    if _configured:
        return

    level = (
        TRACE_LEVEL
        if log_level.upper() == "TRACE"
        else getattr(logging, log_level.upper(), logging.INFO)
    )
    render_json = (not debug) if log_json is None else log_json

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        timestamper,
    ]

    if render_json:
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
        processors = [
            *shared_processors,
            structlog.processors.format_exc_info,
            renderer,
        ]
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)
        processors = [
            *shared_processors,
            renderer,
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(level=level, format="%(message)s")

    logging.getLogger("sqlite3").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)

    _configured = True
