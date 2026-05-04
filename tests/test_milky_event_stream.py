"""Tests for Milky WebSocket event stream."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from nahida_bot.channels.milky.config import parse_milky_config
from nahida_bot.channels.milky.event_stream import MilkyEventStream


class _FakeWebSocket:
    def __init__(self, messages: list[object]) -> None:
        self._messages = messages

    def __aiter__(self) -> _FakeWebSocket:
        return self

    async def __anext__(self) -> object:
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)


class _FakeConnection:
    def __init__(self, messages: list[object]) -> None:
        self.messages = messages
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> _FakeWebSocket:
        self.entered = True
        return _FakeWebSocket(self.messages)

    async def __aexit__(self, *args: object) -> None:
        self.exited = True


@pytest.mark.asyncio
async def test_consume_once_parses_json_events_and_auth_headers() -> None:
    events: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    connection = _FakeConnection(
        [
            '{"event_type": "message_receive", "data": {"message_seq": 1}}',
            b'{"event_type": "bot_online"}',
            "not json",
            '["not an object"]',
        ]
    )

    def connector(url: str, **kwargs: Any) -> _FakeConnection:
        calls.append({"url": url, **kwargs})
        return connection

    async def on_event(event: dict[str, Any]) -> None:
        events.append(event)

    config = parse_milky_config(
        {"base_url": "http://milky.local", "access_token": "secret"}
    )
    stream = MilkyEventStream(config, on_event, connector=connector)

    await stream.consume_once()

    assert connection.entered is True
    assert connection.exited is True
    assert calls == [
        {
            "url": "ws://milky.local/event",
            "open_timeout": 10.0,
            "ping_timeout": 30.0,
            "extra_headers": {"Authorization": "Bearer secret"},
        }
    ]
    assert events == [
        {"event_type": "message_receive", "data": {"message_seq": 1}},
        {"event_type": "bot_online"},
    ]


@pytest.mark.asyncio
async def test_start_and_stop_cancel_background_task() -> None:
    started = asyncio.Event()

    async def on_event(event: dict[str, Any]) -> None:
        return None

    class BlockingConnection:
        async def __aenter__(self) -> object:
            started.set()
            await asyncio.Event().wait()
            return object()

        async def __aexit__(self, *args: object) -> None:
            return None

    def connector(url: str, **kwargs: Any) -> BlockingConnection:
        return BlockingConnection()

    stream = MilkyEventStream(
        parse_milky_config({"base_url": "http://milky.local"}),
        on_event,
        connector=connector,
    )

    await stream.start()
    await asyncio.wait_for(started.wait(), timeout=1)
    assert stream.is_running is True

    await stream.stop()

    assert stream.is_running is False


@pytest.mark.asyncio
async def test_connector_must_return_async_context_manager() -> None:
    async def on_event(event: dict[str, Any]) -> None:
        return None

    def connector(url: str, **kwargs: Any) -> object:
        return object()

    stream = MilkyEventStream(
        parse_milky_config({"base_url": "http://milky.local"}),
        on_event,
        connector=connector,
    )

    with pytest.raises(TypeError, match="async context manager"):
        await stream.consume_once()
