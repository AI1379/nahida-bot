"""Python Node Client SDK for the Gateway-Node protocol.

This is the node-side counterpart to ``nahida_bot.gateway.node_protocol``. It
connects to a Gateway WebSocket endpoint, authenticates, registers its
capabilities, and runs the read loop — dispatching inbound
``capability.invoke`` requests to registered handlers and surfacing inbound
events to the application.

It reuses the same envelope/dispatcher machinery as the gateway side so both
ends stay symmetric. A single ``NodeClient`` instance owns one connection.

Usage::

    client = NodeClient(
        url="ws://127.0.0.1:6185/api/nodes/ws",
        token="nt_xxxx.secret",
        node_id="my-worker",
        node_type="worker",
    )
    client.capabilities.register("worker.run", handler)
    await client.start()  # blocks until stopped or disconnected
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import secrets
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import structlog

from nahida_bot.gateway.node_protocol.dispatcher import (
    InboundHandlerResult,
    NodeDispatcher,
)
from nahida_bot.gateway.node_protocol.schemas import (
    NodeEnvelope,
    HeartbeatPayload,
    NodeRegisterPayload,
    build_heartbeat,
    build_request,
    error_from_exception,
)
from nahida_bot.gateway.node_protocol.sessions import (
    NodeSession,
    NodeSessionState,
)
from nahida_bot.node.capabilities import CapabilityRegistry

logger = structlog.get_logger(__name__)

#: Backoff for reconnect: initial and cap, in seconds.
RECONNECT_INITIAL_DELAY = 1.0
RECONNECT_MAX_DELAY = 30.0


@contextlib.contextmanager
def _swallow():
    """Swallow CancelledError/Exception when tearing down background tasks."""
    try:
        yield
    except (asyncio.CancelledError, Exception):
        pass


class NodeClient:
    """A Gateway-Node protocol client (node side).

    The client is connection-oriented: ``start()`` runs the connect/register/
    read loop until ``stop()`` is called or an unrecoverable error occurs.
    Network failures trigger automatic reconnection with exponential backoff.
    """

    def __init__(
        self,
        *,
        url: str,
        token: str,
        node_id: str,
        node_type: str = "desktop",
        display_name: str = "",
        metadata: dict[str, Any] | None = None,
        capabilities: CapabilityRegistry | None = None,
        heartbeat_interval_ms: int = 15000,
        heartbeat_timeout_ms: int = 45000,
    ) -> None:
        self._url = url
        self._token = token
        self._node_id = node_id
        self._node_type = node_type
        self._display_name = display_name or node_id
        self._metadata = dict(metadata or {})
        self.capabilities = capabilities or CapabilityRegistry()
        self.heartbeat_interval_ms = heartbeat_interval_ms
        self.heartbeat_timeout_ms = heartbeat_timeout_ms

        self._session = NodeSession(session_id="", node_id=node_id)
        self._dispatcher = NodeDispatcher(self._session)
        self._ws: Any = None
        self._connect: Any = None
        self._stopping = asyncio.Event()
        self._registered = asyncio.Event()
        self._on_event_callbacks: list[Any] = []

        self._dispatcher.register_inbound_handler(
            "capability.invoke", self._handle_capability_invoke
        )
        self._dispatcher.register_inbound_handler(
            "capability.cancel", self._handle_capability_cancel
        )

    # -- Public API --------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._session.state in (
            NodeSessionState.ONLINE,
            NodeSessionState.REGISTERING,
        )

    @property
    def registered(self) -> bool:
        return self._session.state == NodeSessionState.ONLINE

    def on_event(self, callback) -> None:
        """Register a callback fired for every inbound gateway event."""
        self._on_event_callbacks.append(callback)

    async def start(self) -> None:
        """Run the connection loop until ``stop()`` is called.

        Reconnects automatically with exponential backoff. Returns cleanly when
        stopped. Auth/registration failures are retried like network errors —
        callers that want fail-fast auth should verify the token out of band.
        """
        delay = RECONNECT_INITIAL_DELAY
        while not self._stopping.is_set():
            try:
                await self._run_once()
                delay = RECONNECT_INITIAL_DELAY  # reset after clean disconnect
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "node_client.connection_failed",
                    node_id=self._node_id,
                    error=str(exc),
                    retry_in=delay,
                )
            if self._stopping.is_set():
                break
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=delay)
            except TimeoutError:
                pass
            delay = min(delay * 2, RECONNECT_MAX_DELAY)

    def stop(self) -> None:
        self._stopping.set()
        self._close_ws_soon()

    async def submit_input(self, *, session_id: str, text: str) -> NodeEnvelope:
        """Submit a user message from this node into a Gateway session."""
        if not self.registered:
            raise RuntimeError("node is not registered")
        request = build_request(
            "node.input.submit",
            request_id=f"req_input_{secrets.token_hex(6)}",
            payload={"session_id": session_id, "text": text},
        )
        return await self._send_request_and_await_response(request, timeout=30.0)

    # -- Single connection lifecycle --------------------------------------

    async def _run_once(self) -> None:
        self._connect = self._load_websockets_connect()
        async with self._connect(self._connect_url()) as ws:
            self._ws = ws
            self._session.state = NodeSessionState.REGISTERING

            # The read loop must run concurrently with registration: the
            # register response arrives as an inbound frame that the read
            # loop feeds into the dispatcher. Starting them together avoids a
            # deadlock where _register() awaits a response nobody is reading.
            read_task = asyncio.create_task(self._read_loop())
            try:
                await self._register()
                heartbeat = asyncio.create_task(self._heartbeat_loop())
                try:
                    await read_task  # runs until the socket closes
                finally:
                    heartbeat.cancel()
                    with _swallow():
                        await heartbeat
            finally:
                read_task.cancel()
                with _swallow():
                    await read_task
                self._ws = None
                self._session.state = NodeSessionState.OFFLINE
                self._registered.clear()

    async def _register(self) -> None:
        request = build_request(
            "node.register",
            request_id=f"req_register_{secrets.token_hex(6)}",
            payload=NodeRegisterPayload(
                node_id=self._node_id,
                display_name=self._display_name,
                node_type=self._node_type,  # type: ignore[arg-type]
                capabilities=self.capabilities.specs(),
                metadata=self._metadata,
            ),
        )
        response = await self._send_request_and_await_response(request, timeout=15.0)
        if not response.ok:
            raise RuntimeError(
                f"registration rejected: {response.error.message if response.error else 'unknown'}"
            )
        payload = response.payload or {}
        self._session.session_id = payload.get("session_id", "")
        heartbeat_interval = payload.get("heartbeat_interval_ms")
        heartbeat_timeout = payload.get("heartbeat_timeout_ms")
        if isinstance(heartbeat_interval, int):
            self.heartbeat_interval_ms = heartbeat_interval
        if isinstance(heartbeat_timeout, int):
            self.heartbeat_timeout_ms = heartbeat_timeout
        self._session.state = NodeSessionState.ONLINE
        self._session.touch()
        self._registered.set()
        logger.info(
            "node_client.registered",
            node_id=self._node_id,
            session_id=self._session.session_id,
        )

    async def _read_loop(self) -> None:
        assert self._ws is not None
        async for raw in self._ws:
            envelope = self._parse_frame(raw)
            if envelope is None:
                continue
            self._session.touch()
            if envelope.kind == "heartbeat":
                await self._handle_heartbeat(envelope)
                continue
            if envelope.kind == "event":
                await self._dispatch_event(envelope)
                continue
            response = await self._dispatcher.handle_inbound(envelope)
            if response is not None and self._ws is not None:
                await self._send(response)

    async def _heartbeat_loop(self) -> None:
        interval = max(self.heartbeat_interval_ms / 1000.0, 1.0)
        timeout = max(self.heartbeat_timeout_ms / 1000.0, interval)
        while not self._stopping.is_set():
            await asyncio.sleep(interval)
            if self._ws is None:
                return
            try:
                elapsed = time.time() - self._session.last_seen_at.timestamp()
                if elapsed > timeout:
                    logger.warning(
                        "node_client.heartbeat_timeout",
                        node_id=self._node_id,
                        elapsed=elapsed,
                        timeout=timeout,
                    )
                    await self._close_ws(self._ws)
                    return
                await self._send(build_heartbeat("ping", ts=int(time.time() * 1000)))
            except Exception:  # noqa: BLE001
                return

    # -- Inbound handlers --------------------------------------------------

    async def _handle_capability_invoke(
        self, session: NodeSession, envelope: NodeEnvelope
    ) -> InboundHandlerResult | None:
        from nahida_bot.gateway.node_protocol.schemas import (
            CapabilityInvokePayload,
            NodeErrorObject,
        )

        payload = envelope.typed_request_payload(CapabilityInvokePayload)
        try:
            result = await self.capabilities.invoke(
                payload.capability, payload.arguments
            )
        except KeyError:
            return InboundHandlerResult(
                ok=False,
                error=NodeErrorObject(
                    code="capability_not_found",
                    message=f"capability {payload.capability} not registered",
                ),
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the gateway
            err = error_from_exception(exc)
            return InboundHandlerResult(ok=False, error=err)
        return InboundHandlerResult(ok=True, payload=result)

    async def _handle_capability_cancel(
        self, session: NodeSession, envelope: NodeEnvelope
    ) -> InboundHandlerResult | None:
        # Capability cancellation is best-effort; handlers must cooperatively
        # check cancellation. V1 acknowledges the cancel.
        return InboundHandlerResult(ok=True, payload={"acknowledged": True})

    async def _handle_heartbeat(self, envelope: NodeEnvelope) -> None:
        try:
            payload = HeartbeatPayload.model_validate(envelope.payload or {})
        except Exception:  # noqa: BLE001 - malformed heartbeat, ignore frame
            return
        if payload.type == "ping":
            await self._send(build_heartbeat("pong", echo_ts=payload.ts))

    async def _dispatch_event(self, envelope: NodeEnvelope) -> None:
        displaced = envelope.event == "node.duplicate_connection"
        if displaced:
            # A duplicate connection means a newer instance owns this node id.
            # Stop instead of reconnecting and repeatedly displacing each other.
            self._stopping.set()
        for callback in list(self._on_event_callbacks):
            try:
                await callback(envelope)
            except Exception:  # noqa: BLE001 - one bad callback must not break others
                logger.exception("node_client.event_callback_failed")
        if displaced and self._ws is not None:
            await self._close_ws(self._ws)

    # -- Low-level send/parse ---------------------------------------------

    async def _send(self, envelope: NodeEnvelope) -> None:
        if self._ws is None:
            raise RuntimeError("not connected")
        await self._ws.send(envelope.model_dump_json(exclude_none=True))

    async def _send_request_and_await_response(
        self, request: NodeEnvelope, *, timeout: float
    ) -> NodeEnvelope:
        request_id = request.id or ""
        future = self._dispatcher.register_pending(request)
        try:
            await self._send(request)
            return await self._dispatcher.wait_pending(
                request_id=request_id,
                future=future,
                timeout=timeout,
            )
        except Exception:
            if not future.done():
                self._dispatcher.cancel_pending(request_id, reason="send failed")
            raise

    def _connect_url(self) -> str:
        if not self._token:
            return self._url
        return _with_query_token(self._url, self._token)

    def _close_ws_soon(self) -> None:
        ws = self._ws
        if ws is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._close_ws(ws))

    async def _close_ws(self, ws: Any) -> None:
        with contextlib.suppress(Exception):
            result = ws.close()
            if inspect.isawaitable(result):
                await result

    @staticmethod
    def _parse_frame(raw: Any) -> NodeEnvelope | None:
        try:
            data = raw if isinstance(raw, str) else raw.decode("utf-8")
            parsed = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
            return None
        try:
            return NodeEnvelope.model_validate(parsed)
        except Exception:  # noqa: BLE001 - drop malformed frames
            return None

    @staticmethod
    def _load_websockets_connect() -> Any:
        import importlib

        try:
            module = importlib.import_module("websockets.asyncio.client")
        except ImportError:
            module = importlib.import_module("websockets")
        return getattr(module, "connect")


def _with_query_token(url: str, token: str) -> str:
    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    query = dict(pairs)
    if "token" not in query and "node_token" not in query:
        query["token"] = token
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


__all__ = ["NodeClient", "RECONNECT_INITIAL_DELAY", "RECONNECT_MAX_DELAY"]
