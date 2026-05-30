"""OneBot v11 WebSocket duplex connection (events + API on the same WS)."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from nahida_bot.channels.onebot.config import OneBotPluginConfig
from nahida_bot.channels.onebot.v11.action import (
    decode_v11_response,
    encode_v11_action,
    is_v11_api_response,
    resolve_v11_action,
)

logger = structlog.get_logger(__name__)

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]
ConnectFactory = Callable[..., Any]
SleepFunc = Callable[[float], Awaitable[None]]


class OneBotV11Connection:
    """Manage a single v11 WebSocket connection for events and API calls.

    v11 uses the same WS connection for both receiving events and sending
    API actions. Responses are matched to pending calls via the ``echo`` field.
    """

    def __init__(
        self,
        config: OneBotPluginConfig,
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
        self._websocket: Any = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._echo_counter = 0
        self._send_lock = asyncio.Lock()
        self._self_id = ""

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def self_id(self) -> str:
        return self._self_id

    async def start(self) -> None:
        if self.is_running:
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
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
        # Fail all pending futures
        for future in self._pending.values():
            if not future.done():
                future.set_exception(RuntimeError("Connection closed"))
        self._pending.clear()

    async def call_action(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send an action over the WS and wait for the response."""
        v11_action = resolve_v11_action(action)
        echo = str(self._echo_counter)
        self._echo_counter += 1

        payload = encode_v11_action(v11_action, params)
        payload["echo"] = echo

        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_event_loop().create_future()
        )
        self._pending[echo] = future

        try:
            async with self._send_lock:
                if self._websocket is None:
                    raise RuntimeError("WebSocket not connected")
                await self._websocket.send(json.dumps(payload, ensure_ascii=False))
        except Exception:
            self._pending.pop(echo, None)
            raise

        try:
            raw_response = await asyncio.wait_for(future, timeout=30.0)
        except asyncio.TimeoutError:
            self._pending.pop(echo, None)
            raise RuntimeError(f"Action {action!r} timed out")
        finally:
            self._pending.pop(echo, None)

        response = decode_v11_response(raw_response)
        if response.status == "failed" or response.retcode != 0:
            raise RuntimeError(
                f"Action {action!r} failed: retcode={response.retcode} "
                f"message={response.message}"
            )
        return raw_response

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
                    "onebot.ws_disconnected",
                    error=str(exc),
                    reconnect_delay=delay,
                )

            if not self._stopping:
                logger.info("onebot.ws_reconnect_scheduled", delay=delay)
                await self._sleep(delay)
                delay = min(delay * 2, self._config.reconnect_max_delay)

    async def _consume_once(self) -> None:
        url = self._config.ws_url
        if not url:
            raise ValueError("ws_url not configured")

        connection = self._open_connection(url)
        if not hasattr(connection, "__aenter__") or not hasattr(
            connection, "__aexit__"
        ):
            raise TypeError("WebSocket connector must return an async context manager")

        async with connection as websocket:
            self._websocket = websocket
            logger.info("onebot.ws_connected", url=_redact_token(url))
            async for raw_message in websocket:
                parsed = self._parse_message(raw_message)
                if parsed is None:
                    continue

                if is_v11_api_response(parsed):
                    self._dispatch_response(parsed)
                else:
                    self._update_self_id(parsed)
                    await self._on_event(parsed)

    def _open_connection(self, url: str) -> Any:
        if self._connector is not None:
            return self._connector(url)

        connect = _load_websockets_connect()
        kwargs: dict[str, Any] = {}
        headers = self._auth_headers()
        kwargs.update(_headers_kwargs_for_connect(connect, headers))
        return connect(url, **kwargs)

    def _auth_headers(self) -> dict[str, str]:
        if not self._config.ws_access_token:
            return {}
        return {"Authorization": f"Bearer {self._config.ws_access_token}"}

    def _parse_message(self, raw_message: object) -> dict[str, Any] | None:
        if isinstance(raw_message, bytes):
            raw_message = raw_message.decode("utf-8")
        if not isinstance(raw_message, str):
            logger.warning("onebot.ws_unsupported_frame", frame_type=type(raw_message))
            return None

        try:
            parsed = json.loads(raw_message)
        except json.JSONDecodeError:
            logger.warning("onebot.ws_invalid_json")
            return None
        if not isinstance(parsed, dict):
            return None
        return parsed

    def _dispatch_response(self, raw: dict[str, Any]) -> None:
        echo = raw.get("echo")
        if echo is None:
            return
        echo_str = str(echo)
        future = self._pending.get(echo_str)
        if future is not None and not future.done():
            future.set_result(raw)

    def _update_self_id(self, raw: dict[str, Any]) -> None:
        sid = raw.get("self_id")
        if sid is not None and str(sid) != self._self_id:
            self._self_id = str(sid)
            logger.info("onebot.self_id_detected", self_id=self._self_id)


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


def _redact_token(url: str) -> str:
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    if "access_token" in query:
        query["access_token"] = "***"
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )
