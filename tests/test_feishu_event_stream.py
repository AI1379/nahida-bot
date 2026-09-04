"""Tests for the Feishu event-stream thread bridge."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest

from nahida_bot.channels.feishu.config import FeishuPluginConfig
from nahida_bot.channels.feishu.event_stream import FeishuEventStream

pytestmark = pytest.mark.asyncio


async def test_events_bridge_from_thread_into_main_loop() -> None:
    received: list[dict[str, Any]] = []
    ready = asyncio.Event()
    release = threading.Event()

    async def on_event(event: dict[str, Any]) -> None:
        received.append(event)
        ready.set()

    def sdk_runner(handle_event: Any) -> None:
        # Simulate the SDK: emit from this thread, then block like start().
        handle_event({"header": {"event_type": "im.message.receive_v1"}, "event": {}})
        release.wait(timeout=5.0)

    stream = FeishuEventStream(FeishuPluginConfig(), on_event, sdk_runner=sdk_runner)
    stream.start()

    try:
        await asyncio.wait_for(ready.wait(), timeout=5.0)
    finally:
        release.set()
        await stream.stop()

    assert received == [
        {"header": {"event_type": "im.message.receive_v1"}, "event": {}}
    ]


async def test_malformed_events_dropped_without_crash() -> None:
    received: list[dict[str, Any]] = []
    ready = asyncio.Event()
    release = threading.Event()

    async def on_event(event: dict[str, Any]) -> None:
        received.append(event)
        ready.set()

    def sdk_runner(handle_event: Any) -> None:
        handle_event({})  # dropped: empty
        handle_event({"event": {"message": {"message_id": "om_1"}}})  # delivered
        release.wait(timeout=5.0)

    stream = FeishuEventStream(FeishuPluginConfig(), on_event, sdk_runner=sdk_runner)
    stream.start()

    try:
        await asyncio.wait_for(ready.wait(), timeout=5.0)
    finally:
        release.set()
        await stream.stop()

    assert received == [{"event": {"message": {"message_id": "om_1"}}}]


async def test_sdk_runner_exception_is_isolated() -> None:
    async def on_event(event: dict[str, Any]) -> None:
        return None

    def sdk_runner(handle_event: Any) -> None:
        raise RuntimeError("sdk blew up")

    stream = FeishuEventStream(FeishuPluginConfig(), on_event, sdk_runner=sdk_runner)
    stream.start()
    deadline = time.monotonic() + 5.0
    while (
        stream._thread is not None
        and stream._thread.is_alive()
        and time.monotonic() < deadline
    ):
        await asyncio.sleep(0.01)
    await stream.stop()  # must not raise


async def test_stop_is_idempotent() -> None:
    async def on_event(event: dict[str, Any]) -> None:
        return None

    def sdk_runner(handle_event: Any) -> None:
        return None

    stream = FeishuEventStream(FeishuPluginConfig(), on_event, sdk_runner=sdk_runner)
    stream.start()
    await stream.stop()
    await stream.stop()
    assert stream.is_running is False
