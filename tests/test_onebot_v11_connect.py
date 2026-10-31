"""Regression tests for the OneBot v11 WebSocket transport.

The v11 socket carries inbound events and API responses on the same
connection. If an event handler is awaited inline in the read loop, the
read loop cannot deliver the action response that the very same handler is
waiting for, and the call times out after 30s. Events must therefore be
handled off the read loop.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
import websockets
from websockets.asyncio.server import serve

from nahida_bot.channels.onebot.config import OneBotPluginConfig
from nahida_bot.channels.onebot.v11.connect import OneBotV11Connection

pytestmark = pytest.mark.asyncio

_EVENT: dict[str, Any] = {
    "time": 1,
    "self_id": 999,
    "post_type": "message",
    "message_type": "private",
    "sub_type": "friend",
    "message_id": 1,
    "user_id": 2,
    "raw_message": "hi",
    "message": [{"type": "text", "data": {"text": "hi"}}],
    "raw": "hi",
}


async def _fake_onebot_server(websocket: Any) -> None:
    await websocket.send(json.dumps(_EVENT))
    async for raw in websocket:
        request = json.loads(raw)
        await websocket.send(
            json.dumps(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": {"message_id": 4242},
                    "echo": request.get("echo"),
                }
            )
        )


async def test_action_call_inside_event_handler_completes() -> None:
    """A reply sent from within an event handler must not deadlock."""
    async with serve(_fake_onebot_server, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        config = OneBotPluginConfig(
            protocol_version="v11", ws_url="ws://127.0.0.1:%d" % port
        )

        result: dict[str, Any] = {}

        async def on_event(parsed: dict[str, Any]) -> None:
            try:
                response = await connection.call_action(
                    "send_msg",
                    {
                        "message_type": "private",
                        "user_id": 2,
                        "message": [{"type": "text", "data": {"text": "reply"}}],
                    },
                )
                result["value"] = response["data"]["message_id"]
            except Exception as exc:  # pragma: no cover - failure detail
                result["error"] = repr(exc)

        connection = OneBotV11Connection(
            config, on_event, connector=lambda url: websockets.connect(url)
        )
        await connection.start()
        try:
            for _ in range(100):
                if result:
                    break
                await asyncio.sleep(0.05)

            assert "error" not in result, result.get("error")
            assert result.get("value") == 4242, result
        finally:
            await connection.stop()


async def test_events_are_handled_in_arrival_order() -> None:
    """Event handling order must be preserved after the transport change."""
    received: list[int] = []

    async def _server(websocket: Any) -> None:
        for index in range(5):
            await websocket.send(json.dumps({**_EVENT, "message_id": index}))
        async for _raw in websocket:
            pass

    async with serve(_server, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        config = OneBotPluginConfig(
            protocol_version="v11", ws_url="ws://127.0.0.1:%d" % port
        )

        async def on_event(parsed: dict[str, Any]) -> None:
            received.append(parsed["message_id"])

        connection = OneBotV11Connection(
            config, on_event, connector=lambda url: websockets.connect(url)
        )
        await connection.start()
        try:
            for _ in range(100):
                if len(received) >= 5:
                    break
                await asyncio.sleep(0.05)
            assert received == [0, 1, 2, 3, 4], received
        finally:
            await connection.stop()
