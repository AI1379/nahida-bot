"""Feishu event stream: SDK WebSocket long-connection bridged into asyncio.

The official SDK's ``lark.ws.Client`` owns a module-level asyncio loop bound
to whichever thread first imports it, ``start()`` blocks that thread forever,
and event handlers are plain sync callbacks. This wrapper therefore:

1. runs the whole SDK client inside one dedicated daemon thread (import
   included, so the SDK loop binds to that thread);
2. marshals each event dict into the bot's main loop via
   ``asyncio.run_coroutine_threadsafe`` and returns immediately, satisfying
   Feishu's "handle within 3 seconds" ACK expectation;
3. stops the SDK loop with ``loop.stop()`` on shutdown because the SDK has
   no public stop API.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine
from typing import Any

import structlog

from nahida_bot.channels.feishu.config import FeishuPluginConfig

EventHandler = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]
# Injectable SDK bootstrap: receives the sync event callback and blocks until
# the stream stops. Tests substitute this to avoid importing lark-oapi.
SdkRunner = Callable[[Callable[[dict[str, Any]], None]], None]

logger = structlog.get_logger(__name__)


class FeishuEventStream:
    """Run the lark-oapi WebSocket client on a dedicated thread."""

    def __init__(
        self,
        config: FeishuPluginConfig,
        on_event: EventHandler,
        *,
        sdk_runner: SdkRunner | None = None,
    ) -> None:
        self._config = config
        self._on_event = on_event
        self._sdk_runner = sdk_runner
        self._thread: threading.Thread | None = None
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._sdk_loop: asyncio.AbstractEventLoop | None = None
        self._sdk_client: Any = None
        self._stopping = False

    @property
    def is_running(self) -> bool:
        """Whether the SDK thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Start the background SDK thread (must be called on the main loop)."""
        if self.is_running:
            return
        self._main_loop = asyncio.get_running_loop()
        self._stopping = False
        self._thread = threading.Thread(
            target=self._thread_main,
            name="feishu-ws-client",
            daemon=True,
        )
        self._thread.start()

    async def stop(self) -> None:
        """Stop the SDK thread (best-effort; the SDK has no public stop)."""
        self._stopping = True
        loop = self._sdk_loop
        client = self._sdk_client
        if loop is not None and loop.is_running():
            if client is not None:
                # Close the socket first so reconnect logic does not kick in.
                disconnect = getattr(client, "_disconnect", None)
                if disconnect is not None:
                    try:
                        asyncio.run_coroutine_threadsafe(disconnect(), loop)
                    except RuntimeError:
                        pass
            # run_until_complete(_select()) inside start() unwinds on stop().
            loop.call_soon_threadsafe(loop.stop)
        thread = self._thread
        if (
            thread is not None
            and thread.is_alive()
            and threading.current_thread() is not thread
        ):
            thread.join(timeout=5.0)
        self._thread = None
        self._sdk_loop = None
        self._sdk_client = None

    # ── internals ─────────────────────────────────────────────────

    def _thread_main(self) -> None:
        runner = self._sdk_runner or self._default_sdk_runner
        try:
            runner(self._make_sync_handler())
        except Exception as exc:  # noqa: BLE001 - thread must never crash silently
            if not self._stopping:
                logger.error("feishu.ws_thread_exited", error=str(exc))

    def _make_sync_handler(self) -> Callable[[dict[str, Any]], None]:
        """Build the sync callback that bridges events into the main loop."""
        loop = self._main_loop
        if loop is None:
            raise RuntimeError("FeishuEventStream.start() must run on the main loop")

        def handle_event(event: dict[str, Any]) -> None:
            if self._stopping or not event:
                return
            try:
                asyncio.run_coroutine_threadsafe(self._on_event(event), loop)
            except RuntimeError as exc:
                if not self._stopping:
                    logger.warning("feishu.event_bridge_failed", error=str(exc))

        return handle_event

    def _default_sdk_runner(
        self, handle_event: Callable[[dict[str, Any]], None]
    ) -> None:
        """Import the SDK inside this thread, build the ws client, and start it.

        Importing here (not at module import time) is load-bearing: the SDK
        binds its module-level asyncio loop to the importing thread, and it
        must own this worker thread rather than the bot's main thread.
        """
        import json

        import lark_oapi as lark

        def handle_receive(data: Any) -> None:
            try:
                payload = lark.JSON.marshal(data)
                raw = json.loads(payload) if payload else None
            except Exception:  # noqa: BLE001 - malformed event must not kill the loop
                logger.warning("feishu.event_marshal_failed")
                return
            if isinstance(raw, dict):
                handle_event(raw)

        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(handle_receive)
            .build()
        )
        client = lark.ws.Client(
            self._config.app_id,
            self._config.app_secret,
            event_handler=event_handler,
            domain=self._config.domain,
            auto_reconnect=True,
            log_level=lark.LogLevel.INFO,
        )
        self._sdk_loop = asyncio.get_event_loop()
        self._sdk_client = client
        logger.info(
            "feishu.ws_connected",
            domain=self._config.domain,
            app_id=self._config.app_id[:8] + "…",
        )
        client.start()  # blocks until loop.stop()
        logger.info("feishu.ws_stopped")
