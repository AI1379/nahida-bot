"""Logging setup helpers for Nahida Bot."""

from __future__ import annotations

import logging

import structlog

_configured = False


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

    level = getattr(logging, log_level.upper(), logging.INFO)
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
    _configured = True
