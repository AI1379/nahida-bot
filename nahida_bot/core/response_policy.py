"""Policy for runtime failure notices, separate from ordinary model dialogue."""

from __future__ import annotations

from nahida_bot.agent.memory.store import MemoryStore
from nahida_bot.core.runtime_settings import (
    ErrorNotificationMode,
    runtime_settings_from_meta,
)

import structlog

logger = structlog.get_logger(__name__)


def is_failed_response(terminal_state: str, error: str | None) -> bool:
    """Use runtime outcomes, never words such as 'error' in generated text."""
    return bool(error) or terminal_state in {"failed", "incomplete", "cancelled"}


def allows_error_notification(mode: ErrorNotificationMode, trigger_kind: str) -> bool:
    """Only explicitly addressed interactions notify by default."""
    if mode == "none":
        return False
    return mode == "all" or trigger_kind in {
        "private",
        "mention",
        "command",
        "node_input",
        "voice_input",
    }


async def should_notify_error(
    memory: MemoryStore | None, session_id: str, *, trigger_kind: str
) -> bool:
    """Read the latest session choice at delivery time; fail quietly on I/O errors."""
    mode: ErrorNotificationMode = "interactive"
    if memory is not None:
        try:
            settings = runtime_settings_from_meta(
                await memory.get_session_meta(session_id)
            )
            mode = settings.error_notifications
        except Exception:
            logger.warning(
                "response_policy.settings_unavailable",
                session_id=session_id,
                exc_info=True,
            )
            return False
    allowed = allows_error_notification(mode, trigger_kind)
    if not allowed:
        logger.info(
            "response_policy.error_suppressed",
            session_id=session_id,
            trigger_kind=trigger_kind,
            mode=mode,
        )
    return allowed
