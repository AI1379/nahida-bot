"""Tests for SessionRunner active-run tracking and graceful stop."""

from __future__ import annotations

import asyncio

import pytest

from nahida_bot.core.session_runner import ActiveRunTracker


@pytest.mark.asyncio
async def test_request_stop_sets_event_without_cancelling_task() -> None:
    """Stopping a run signals stop_event and leaves the task alive to finish.

    Regression guard for the /stop context-loss bug: ``request_stop``
    previously called ``task.cancel()``, which injected ``CancelledError``
    mid-run so SessionRunner skipped ``_persist_turns`` and dropped the user
    message plus any partial reply from history. The graceful stop path only
    works if the task survives long enough to reach the loop's ``stop_event``
    check and emit its ``done`` event, which is what persists the turn.
    """
    tracker = ActiveRunTracker()
    stop_event = asyncio.Event()

    async def worker() -> str:
        # Emulate the agent loop blocking on its next step boundary.
        await stop_event.wait()
        return "completed-gracefully"

    task = asyncio.create_task(worker())
    tracker.start("session-1", task, stop_event)

    assert tracker.request_stop("session-1") is True
    assert stop_event.is_set()

    # Before the fix this awaited task raised CancelledError.
    result = await task
    assert result == "completed-gracefully"
    assert not task.cancelled()


@pytest.mark.asyncio
async def test_request_stop_unknown_session_returns_false() -> None:
    tracker = ActiveRunTracker()
    assert tracker.request_stop("unknown") is False
