"""Tests for WebUI SSE broadcaster behavior."""

import logging
from unittest.mock import MagicMock

from nahida_bot.gateway.services.event_broadcaster import (
    _CLIENT_QUEUE_MAXSIZE,
    _should_bridge_log_record,
    EventBroadcaster,
)


def test_client_queue_is_bounded_and_drops_oldest_events() -> None:
    broadcaster = EventBroadcaster(MagicMock())
    q = broadcaster.subscribe()

    for i in range(_CLIENT_QUEUE_MAXSIZE + 1):
        broadcaster._push_event("test.event", {"i": i})

    assert q.qsize() == _CLIENT_QUEUE_MAXSIZE
    first = q.get_nowait()
    assert first is not None
    assert '"i": 1' in first


def test_cron_updated_event_uses_existing_frontend_contract() -> None:
    broadcaster = EventBroadcaster(MagicMock())
    q = broadcaster.subscribe()

    broadcaster.notify_cron_updated("job-1", "cancelled")

    payload = q.get_nowait()
    assert payload is not None
    assert payload.startswith("event: cron.updated\n")
    assert '"job_id": "job-1"' in payload
    assert '"action": "cancelled"' in payload


def test_log_bridge_ignores_sse_transport_records() -> None:
    record = logging.LogRecord(
        "sse_starlette.sse",
        logging.DEBUG,
        __file__,
        1,
        "chunk: %r",
        (b'event: log.entry\r\ndata: {"event": "x"}\r\n\r\n',),
        None,
    )

    assert _should_bridge_log_record(record) is False


def test_log_bridge_keeps_application_records() -> None:
    record = logging.LogRecord(
        "nahida_bot.gateway.routes.config",
        logging.INFO,
        __file__,
        1,
        "config saved",
        (),
        None,
    )

    assert _should_bridge_log_record(record) is True
