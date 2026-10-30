"""Channel delivery for subagent completion notifications (issue #41).

The orchestrator writes the completion result to the parent session memory
unconditionally, but a memory turn is invisible to a chat user until they
next message the bot. This module bridges the subagent's synthetic
``platform=agent`` context back to the real channel the user spoke to, using
the stable ``delivery_target`` captured at spawn time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from nahida_bot.agent.orchestration.models import AgentRunStatus, BackgroundTask
from nahida_bot.core.outbound import outbound_payload, send_channel_message
from nahida_bot.core.system_text import format_system_text

if TYPE_CHECKING:
    from nahida_bot.core.channel_registry import ChannelRegistry
    from nahida_bot.core.events import EventBus
    from nahida_bot_sdk import OutboundMessage

logger = structlog.get_logger(__name__)

# Keep the delivered notification small — the parent agent will incorporate
# the full result into the next turn via the parent-session memory write.
_MAX_DELIVERED_SUMMARY_CHARS = 800


def _format_notification(
    task: BackgroundTask, status: AgentRunStatus, summary: str, error: str
) -> str:
    title = task.title or task.task_id
    if status == AgentRunStatus.SUCCEEDED:
        headline = "Completed"
        body = summary.strip()
    elif status == AgentRunStatus.CANCELLED:
        headline = "Cancelled"
        body = error.strip()
    elif status == AgentRunStatus.TIMED_OUT:
        headline = "Timed out"
        body = error.strip()
    else:
        headline = "Failed"
        body = (error or summary).strip()
    if body and len(body) > _MAX_DELIVERED_SUMMARY_CHARS:
        body = body[: _MAX_DELIVERED_SUMMARY_CHARS - 1].rstrip() + "…"
    return format_system_text(
        "Task",
        headline,
        fields=(("Task", task.task_id), ("Name", title)),
        body=body,
    )


class ChannelCompletionDeliverer:
    """Deliver subagent completions to the originating chat channel.

    Looks up the channel service via :class:`ChannelRegistry` and sends a
    concise notification. Returns ``True`` only when a channel successfully
    accepted the message, so the orchestrator confirms the claim only after
    the channel reports a message id. A missing channel, rejected send, or
    exception returns ``False`` so the claim can be released for retry.
    """

    def __init__(
        self,
        channel_registry: ChannelRegistry,
        *,
        message_factory: Any | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._channel_registry = channel_registry
        self._event_bus = event_bus
        # ``message_factory`` lets tests inject an OutboundMessage builder
        # without depending on the SDK's constructor signature.
        self._message_factory = message_factory

    async def deliver(
        self,
        *,
        task: BackgroundTask,
        status: AgentRunStatus,
        summary: str,
        error: str,
    ) -> bool:
        target = task.delivery_target
        if not target:
            return False
        channel_name = target.get("channel", "")
        recipient = target.get("target", "")
        if not channel_name or not recipient:
            return False

        text = _format_notification(task, status, summary, error)
        message = self._build_message(
            text,
            chat_address=target.get("chat_address", ""),
        )
        try:
            await send_channel_message(
                self._channel_registry,
                channel_name,
                recipient,
                message,
                event_bus=self._event_bus,
                source="subagent_completion",
                payload=outbound_payload(
                    recipient, channel_name, message, notification=True
                ),
            )
        except Exception:
            logger.exception(
                "subagent.delivery_send_failed",
                task_id=task.task_id,
                channel=channel_name,
            )
            return False
        return True

    def _build_message(self, text: str, *, chat_address: str = "") -> OutboundMessage:
        if self._message_factory is not None:
            message = self._message_factory(text)
            if chat_address:
                extra = getattr(message, "extra", None)
                if isinstance(extra, dict):
                    extra["chat_address"] = chat_address
            return message
        from nahida_bot_sdk import OutboundMessage

        extra = {"chat_address": chat_address} if chat_address else {}
        return OutboundMessage(text=text, extra=extra)
