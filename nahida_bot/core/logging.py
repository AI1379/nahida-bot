"""Logging setup helpers for Nahida Bot."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

import structlog

_configured = False
_HANDLER_ATTR = "_nahida_bot_handler"
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


def _level_from_name(log_level: str) -> int:
    if log_level.upper() == "TRACE":
        return TRACE_LEVEL
    level = logging.getLevelName(log_level.upper())
    return level if isinstance(level, int) else logging.INFO


def _remove_existing_handlers(root_logger: logging.Logger) -> None:
    for handler in list(root_logger.handlers):
        if not getattr(handler, _HANDLER_ATTR, False):
            continue
        root_logger.removeHandler(handler)
        handler.close()


def configure_logging(
    *,
    debug: bool,
    log_level: str = "INFO",
    log_json: bool | None = None,
    log_file: str | None = None,
    log_file_level: str | None = None,
    log_file_json: bool = True,
) -> None:
    """Configure stdlib logging + structlog processors once per process."""
    global _configured
    if _configured:
        return

    console_level = _level_from_name(log_level)
    file_level = _level_from_name(log_file_level or log_level)
    producer_level = min(console_level, file_level) if log_file else console_level
    render_console_json = (not debug) if log_json is None else log_json

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        timestamper,
    ]

    def formatter(
        *,
        render_json: bool,
        colors: bool = False,
    ) -> structlog.stdlib.ProcessorFormatter:
        renderer: structlog.types.Processor
        if render_json:
            renderer = structlog.processors.JSONRenderer()
            processors: list[structlog.types.Processor] = [
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.format_exc_info,
                renderer,
            ]
        else:
            renderer = structlog.dev.ConsoleRenderer(colors=colors)
            processors = [
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ]
        return structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=processors,
        )

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(producer_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    root_logger = logging.getLogger()
    _remove_existing_handlers(root_logger)
    root_logger.setLevel(producer_level)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(
        formatter(render_json=render_console_json, colors=not render_console_json)
    )
    setattr(console_handler, _HANDLER_ATTR, True)
    root_logger.addHandler(console_handler)

    if log_file:
        log_path = Path(log_file).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(file_level)
        file_handler.setFormatter(formatter(render_json=log_file_json, colors=False))
        setattr(file_handler, _HANDLER_ATTR, True)
        root_logger.addHandler(file_handler)

    logging.getLogger("sqlite3").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)

    _configured = True
