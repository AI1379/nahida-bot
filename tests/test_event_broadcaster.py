"""Tests for WebUI SSE broadcaster behavior."""

import logging
from unittest.mock import MagicMock

import pytest

from nahida_bot.core.events import (
    AgentRunFinished,
    AgentRunPayload,
    AgentStopPayload,
    AgentStopRequested,
)
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


@pytest.mark.asyncio
async def test_agent_stop_requested_event_is_broadcast_without_run_payload_fields() -> (
    None
):
    broadcaster = EventBroadcaster(MagicMock())
    q = broadcaster.subscribe()

    await broadcaster._on_agent_run_event(
        AgentStopRequested(payload=AgentStopPayload(session_id="s1")),
        None,
    )

    payload = q.get_nowait()
    assert payload is not None
    assert payload.startswith("event: agent_run.stop_requested\n")
    assert '"session_id": "s1"' in payload
    assert '"workspace_id": ""' in payload


@pytest.mark.asyncio
async def test_agent_finished_event_includes_terminal_and_error() -> None:
    broadcaster = EventBroadcaster(MagicMock())
    q = broadcaster.subscribe()

    await broadcaster._on_agent_run_event(
        AgentRunFinished(
            payload=AgentRunPayload(
                session_id="s1",
                workspace_id="default",
                terminal="failed",
                error="provider_auth_failed",
            )
        ),
        None,
    )

    payload = q.get_nowait()
    assert payload is not None
    assert payload.startswith("event: agent_run.finished\n")
    assert '"terminal": "failed"' in payload
    assert '"error": "provider_auth_failed"' in payload
