"""Logging setup helpers for Nahida Bot."""

from __future__ import annotations

import json
import logging
from collections import deque
from pathlib import Path
from typing import Any, cast

import structlog

_configured = False
_HANDLER_ATTR = "_nahida_bot_handler"
TRACE_LEVEL = 5
logging.addLevelName(TRACE_LEVEL, "TRACE")

_NOISY_DEPENDENCY_LOGGERS: tuple[str, ...] = (
    "asyncio",
    "aiogram",
    "aiohttp",
    "aiosqlite",
    "anyio",
    "httpcore",
    "httpx",
    "mcp",
    "multipart",
    "openai",
    "sse_starlette",
    "sqlite3",
    "urllib3",
    "uvicorn",
    "uvicorn.access",
    "uvicorn.error",
    "watchfiles",
    "websockets",
)

_APPLICATION_LOGGER_PREFIXES: tuple[str, ...] = (
    "nahida_bot",
    "nahida_bot_sdk",
    "plugin",
)


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


def _matches_logger_prefix(logger_name: str, prefix: str) -> bool:
    return logger_name == prefix or logger_name.startswith(prefix + ".")


def _is_application_logger(logger_name: str) -> bool:
    return any(
        _matches_logger_prefix(logger_name, prefix)
        for prefix in _APPLICATION_LOGGER_PREFIXES
    ) or logger_name.startswith("nahida_plugin_")


def _minimum_level(*levels: int, overrides: dict[str, str] | None = None) -> int:
    candidates = list(levels)
    candidates.extend(
        _level_from_name(level_name) for level_name in (overrides or {}).values()
    )
    return min(candidates)


class _PrefixLevelFilter(logging.Filter):
    def __init__(
        self,
        *,
        default_level: int,
        dependency_level: int,
        overrides: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self._default_level = default_level
        self._dependency_level = dependency_level
        self._overrides = {
            name: _level_from_name(level_name)
            for name, level_name in (overrides or {}).items()
        }

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= self._level_for(record.name)

    def _level_for(self, logger_name: str) -> int:
        override_match: tuple[int, int] | None = None
        for prefix, level in self._overrides.items():
            if not _matches_logger_prefix(logger_name, prefix):
                continue
            if override_match is None or len(prefix) > override_match[0]:
                override_match = (len(prefix), level)
        if override_match is not None:
            return override_match[1]

        return (
            self._default_level
            if _is_application_logger(logger_name)
            else self._dependency_level
        )


def _apply_logger_levels(
    *,
    default_dependency_level: int,
    overrides: dict[str, str] | None = None,
) -> None:
    for name in _NOISY_DEPENDENCY_LOGGERS:
        logging.getLogger(name).setLevel(default_dependency_level)

    for name, level_name in (overrides or {}).items():
        logging.getLogger(name).setLevel(_level_from_name(level_name))


def _remove_existing_handlers(root_logger: logging.Logger) -> None:
    for handler in list(root_logger.handlers):
        if not getattr(handler, _HANDLER_ATTR, False):
            continue
        root_logger.removeHandler(handler)
        handler.close()


class InMemoryLogCapture(logging.Handler):
    """Bounded in-memory handler that stores structured JSON log entries."""

    def __init__(self, max_entries: int = 2000) -> None:
        super().__init__()
        self._entries: deque[dict[str, Any]] = deque(maxlen=max_entries)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            raw = self.format(record)
            entry = json.loads(raw)
            entry["logger"] = record.name
            self._entries.append(entry)
        except Exception:
            pass

    def get_entries(self) -> list[dict[str, Any]]:
        return list(self._entries)


_capture_handler: InMemoryLogCapture | None = None


def get_log_capture() -> InMemoryLogCapture | None:
    return _capture_handler


def configure_logging(
    *,
    debug: bool,
    log_level: str = "INFO",
    log_json: bool | None = None,
    log_file: str | None = None,
    log_file_level: str | None = None,
    log_file_json: bool = True,
    dependency_log_level: str = "WARNING",
    logger_levels: dict[str, str] | None = None,
) -> None:
    """Configure stdlib logging + structlog processors once per process."""
    global _configured
    if _configured:
        return

    console_level = _level_from_name(log_level)
    file_level = _level_from_name(log_file_level or log_level)
    dependency_level = _level_from_name(dependency_log_level)
    capture_base_level = min(console_level, file_level) if log_file else console_level
    producer_level = _minimum_level(
        capture_base_level,
        dependency_level,
        overrides=logger_levels,
    )
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
    console_handler.setLevel(
        _minimum_level(console_level, dependency_level, overrides=logger_levels)
    )
    console_handler.addFilter(
        _PrefixLevelFilter(
            default_level=console_level,
            dependency_level=dependency_level,
            overrides=logger_levels,
        )
    )
    console_handler.setFormatter(
        formatter(render_json=render_console_json, colors=not render_console_json)
    )
    setattr(console_handler, _HANDLER_ATTR, True)
    root_logger.addHandler(console_handler)

    if log_file:
        log_path = Path(log_file).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(
            _minimum_level(file_level, dependency_level, overrides=logger_levels)
        )
        file_handler.addFilter(
            _PrefixLevelFilter(
                default_level=file_level,
                dependency_level=dependency_level,
                overrides=logger_levels,
            )
        )
        file_handler.setFormatter(formatter(render_json=log_file_json, colors=False))
        setattr(file_handler, _HANDLER_ATTR, True)
        root_logger.addHandler(file_handler)

    # In-memory log capture for the web UI log viewer
    global _capture_handler
    _capture_handler = InMemoryLogCapture(max_entries=2000)
    _capture_handler.setLevel(producer_level)
    _capture_handler.addFilter(
        _PrefixLevelFilter(
            default_level=capture_base_level,
            dependency_level=dependency_level,
            overrides=logger_levels,
        )
    )
    _capture_handler.setFormatter(formatter(render_json=True, colors=False))
    setattr(_capture_handler, _HANDLER_ATTR, True)
    root_logger.addHandler(_capture_handler)

    _apply_logger_levels(
        default_dependency_level=dependency_level,
        overrides=logger_levels,
    )

    _configured = True
