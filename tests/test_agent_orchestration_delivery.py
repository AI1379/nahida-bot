"""Tests for the channel completion deliverer (issue #41)."""

from __future__ import annotations

from typing import Any

import pytest

from nahida_bot.agent.orchestration.delivery import (
    ChannelCompletionDeliverer,
    _format_notification,
)
from nahida_bot.agent.orchestration.models import (
    AgentRunStatus,
    BackgroundTask,
    TaskRuntime,
)
from nahida_bot.core.channel_registry import ChannelRegistry

from .helpers import StubChannelService


def _task(
    *,
    delivery_target: dict[str, str] | None = None,
    title: str = "research X",
    task_id: str = "task_abc",
) -> BackgroundTask:
    return BackgroundTask(
        task_id=task_id,
        runtime=TaskRuntime.SUBAGENT,
        status=AgentRunStatus.SUCCEEDED,
        requester_session_id="telegram:private:1",
        child_session_id="telegram:private:1:subagent:task_abc",
        parent_task_id=None,
        title=title,
        delivery_target=delivery_target,
    )


# --- notification formatting -------------------------------------------------


def test_notification_success_includes_summary() -> None:
    text = _format_notification(
        _task(title="Summarize PR"),
        AgentRunStatus.SUCCEEDED,
        summary="PR adds tests.",
        error="",
    )
    assert "Summarize PR" in text
    assert "PR adds tests." in text
    assert "done" in text.lower()


def test_notification_failure_includes_error() -> None:
    text = _format_notification(
        _task(),
        AgentRunStatus.FAILED,
        summary="",
        error="provider_error",
    )
    assert "failed" in text.lower()
    assert "provider_error" in text


def test_notification_truncates_long_summary() -> None:
    long = "x" * 5000
    text = _format_notification(_task(), AgentRunStatus.SUCCEEDED, long, "")
    assert len(text) < 5000
    assert text.endswith("…")


# --- ChannelCompletionDeliverer --------------------------------------------


@pytest.mark.asyncio
async def test_deliverer_sends_to_captured_channel() -> None:
    """Issue #41: the deliverer routes to the channel captured at spawn."""
    registry = ChannelRegistry()
    stub = StubChannelService(channel_id="telegram")
    # Record the message instead of returning a fixed id.
    sent: list[tuple[str, Any]] = []

    async def _send(target: str, message: Any) -> str:
        sent.append((target, message))
        return "msg_1"

    stub.send_message = _send  # type: ignore[assignment]
    registry.register(stub)

    deliverer = ChannelCompletionDeliverer(registry)
    ok = await deliverer.deliver(
        task=_task(delivery_target={"channel": "telegram", "target": "42"}),
        status=AgentRunStatus.SUCCEEDED,
        summary="done",
        error="",
    )

    assert ok is True
    assert len(sent) == 1
    target, message = sent[0]
    assert target == "42"
    assert "done" in message.text


@pytest.mark.asyncio
async def test_deliverer_preserves_typed_chat_address() -> None:
    """Group/private routing metadata must survive background delivery."""
    registry = ChannelRegistry()
    stub = StubChannelService(channel_id="onebot")
    sent: list[tuple[str, Any]] = []

    async def _send(target: str, message: Any) -> str:
        sent.append((target, message))
        return "msg_1"

    stub.send_message = _send  # type: ignore[assignment]
    registry.register(stub)
    deliverer = ChannelCompletionDeliverer(registry)

    ok = await deliverer.deliver(
        task=_task(
            delivery_target={
                "channel": "onebot",
                "target": "42",
                "chat_address": "onebot:group:42",
            }
        ),
        status=AgentRunStatus.SUCCEEDED,
        summary="done",
        error="",
    )

    assert ok is True
    assert sent[0][0] == "42"
    assert sent[0][1].extra["chat_address"] == "onebot:group:42"


@pytest.mark.asyncio
async def test_deliverer_returns_false_for_empty_message_id() -> None:
    """Channels use an empty id to signal that no message was accepted."""
    registry = ChannelRegistry()
    stub = StubChannelService(channel_id="onebot")

    async def _not_sent(target: str, message: Any) -> str:
        return ""

    stub.send_message = _not_sent  # type: ignore[assignment]
    registry.register(stub)
    deliverer = ChannelCompletionDeliverer(registry)

    ok = await deliverer.deliver(
        task=_task(delivery_target={"channel": "onebot", "target": "42"}),
        status=AgentRunStatus.SUCCEEDED,
        summary="done",
        error="",
    )

    assert ok is False


@pytest.mark.asyncio
async def test_deliverer_returns_false_when_channel_missing() -> None:
    """Issue #41: a missing channel leaves the task undelivered for retry."""
    registry = ChannelRegistry()  # no channels registered
    deliverer = ChannelCompletionDeliverer(registry)

    ok = await deliverer.deliver(
        task=_task(delivery_target={"channel": "telegram", "target": "42"}),
        status=AgentRunStatus.SUCCEEDED,
        summary="done",
        error="",
    )

    assert ok is False


@pytest.mark.asyncio
async def test_deliverer_returns_false_when_send_raises() -> None:
    """Issue #41: a send exception is swallowed; the task stays undelivered."""
    registry = ChannelRegistry()
    stub = StubChannelService(channel_id="telegram")

    async def _boom(target: str, message: Any) -> str:
        raise RuntimeError("channel down")

    stub.send_message = _boom  # type: ignore[assignment]
    registry.register(stub)

    deliverer = ChannelCompletionDeliverer(registry)
    ok = await deliverer.deliver(
        task=_task(delivery_target={"channel": "telegram", "target": "42"}),
        status=AgentRunStatus.SUCCEEDED,
        summary="done",
        error="",
    )

    assert ok is False


@pytest.mark.asyncio
async def test_deliverer_returns_false_without_target() -> None:
    """Issue #41: a task with no delivery target (synthetic session) is skipped."""
    registry = ChannelRegistry()
    registry.register(StubChannelService(channel_id="telegram"))
    deliverer = ChannelCompletionDeliverer(registry)

    ok = await deliverer.deliver(
        task=_task(delivery_target=None),
        status=AgentRunStatus.SUCCEEDED,
        summary="done",
        error="",
    )

    assert ok is False
