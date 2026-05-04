"""WebSocket event stream for Milky ``/event``."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import structlog

from nahida_bot.channels.milky.config import MilkyPluginConfig

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]
ConnectFactory = Callable[..., Any]
SleepFunc = Callable[[float], Awaitable[None]]

logger = structlog.get_logger(__name__)


class MilkyEventStream:
    """Maintain a reconnecting WebSocket connection to Milky ``/event``."""

    def __init__(
        self,
        config: MilkyPluginConfig,
        on_event: EventHandler,
        *,
        connector: ConnectFactory | None = None,
        sleep: SleepFunc = asyncio.sleep,
    ) -> None:
        self._config = config
        self._on_event = on_event
        self._connector = connector
        self._sleep = sleep
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    @property
    def is_running(self) -> bool:
        """Whether the background stream task is active."""
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        """Start the background event stream task."""
        if self.is_running:
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop the background event stream task and close the socket."""
        self._stopping = True
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def consume_once(self) -> None:
        """Consume one WebSocket connection until it closes.

        This is useful for focused tests and diagnostics. Normal runtime should
        use ``start()`` so reconnect behavior is active.
        """
        await self._consume_once()

    async def _run_loop(self) -> None:
        delay = self._config.reconnect_initial_delay
        while not self._stopping:
            try:
                await self._consume_once()
                delay = self._config.reconnect_initial_delay
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "milky.ws_disconnected",
                    error=str(exc),
                    reconnect_delay=delay,
                )

            if not self._stopping:
                logger.info("milky.ws_reconnect_scheduled", delay=delay)
                await self._sleep(delay)
                delay = min(delay * 2, self._config.reconnect_max_delay)

    async def _consume_once(self) -> None:
        url = self._event_url()
        connection = self._open_connection(url)
        if not hasattr(connection, "__aenter__") or not hasattr(
            connection, "__aexit__"
        ):
            raise TypeError(
                "Milky WebSocket connector must return an async context manager"
            )

        async with connection as websocket:
            logger.info("milky.ws_connected", url=_redact_access_token(url))
            async for raw_message in websocket:
                event = self._parse_event(raw_message)
                if event is not None:
                    await self._on_event(event)

    def _open_connection(self, url: str) -> Any:
        if self._connector is not None:
            return self._connector(
                url,
                open_timeout=self._config.connect_timeout,
                ping_timeout=self._config.heartbeat_timeout,
                **self._auth_header_kwargs(),
            )

        connect = _load_websockets_connect()
        kwargs: dict[str, Any] = {
            "open_timeout": self._config.connect_timeout,
            "ping_timeout": self._config.heartbeat_timeout,
        }
        kwargs.update(_headers_kwargs_for_connect(connect, self._auth_headers()))
        if self._config.access_token and not any(
            key in kwargs for key in ("additional_headers", "extra_headers")
        ):
            # Older or custom websockets connectors may not expose a header
            # parameter. Milky also accepts access_token in the query string.
            url = self._event_url(use_query_token=True)
        return connect(url, **kwargs)

    def _auth_header_kwargs(self) -> dict[str, Any]:
        headers = self._auth_headers()
        return {"extra_headers": headers} if headers else {}

    def _auth_headers(self) -> dict[str, str]:
        if not self._config.access_token:
            return {}
        return {"Authorization": f"Bearer {self._config.access_token}"}

    def _event_url(self, *, use_query_token: bool = False) -> str:
        url = self._config.event_ws_url
        if use_query_token and self._config.access_token:
            return _with_query_param(url, "access_token", self._config.access_token)
        return url

    def _parse_event(self, raw_message: object) -> dict[str, Any] | None:
        if isinstance(raw_message, bytes):
            raw_message = raw_message.decode("utf-8")
        if not isinstance(raw_message, str):
            logger.warning("milky.ws_unsupported_frame", frame_type=type(raw_message))
            return None

        try:
            event = json.loads(raw_message)
        except json.JSONDecodeError:
            logger.warning("milky.ws_invalid_json")
            return None
        if not isinstance(event, dict):
            logger.warning("milky.ws_invalid_event", event_type=type(event))
            return None
        return event


def _load_websockets_connect() -> Any:
    try:
        module = importlib.import_module("websockets.asyncio.client")
        return getattr(module, "connect")
    except (ImportError, AttributeError):
        module = importlib.import_module("websockets")
        return getattr(module, "connect")


def _headers_kwargs_for_connect(
    connect: Any, headers: dict[str, str]
) -> dict[str, Any]:
    if not headers:
        return {}
    try:
        parameters = inspect.signature(connect).parameters
    except (TypeError, ValueError):
        return {"extra_headers": headers}
    if "additional_headers" in parameters:
        return {"additional_headers": headers}
    if "extra_headers" in parameters:
        return {"extra_headers": headers}
    return {}


def _with_query_param(url: str, key: str, value: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query[key] = value
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def _redact_access_token(url: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    if "access_token" in query:
        query["access_token"] = "***"
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )
