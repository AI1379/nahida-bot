"""Session-local rolling summaries, checkpointed without altering raw history."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from nahida_bot.agent.compaction import (
    HISTORY_TIMESTAMP,
    ModelCompactor,
    split_history,
    summary_message,
    without_history_timestamps,
)
from nahida_bot.agent.context import (
    ContextBuilder,
    ContextMessage,
    truncate_messages_to_window,
)

if TYPE_CHECKING:
    from nahida_bot.agent.memory.store import MemoryStore
    from nahida_bot.agent.providers.base import ChatProvider

logger = structlog.get_logger(__name__)
_STATE_KEY = "context_compaction"


@dataclass(frozen=True)
class HistoryCompactionScope:
    session_id: str
    mode: str
    scope_start: str
    max_messages: int
    model: str | None = None
    stop_event: asyncio.Event | None = None


class SessionHistoryCompactor:
    def __init__(
        self, memory: MemoryStore, builder: ContextBuilder, provider: ChatProvider
    ):
        self.memory = memory
        self.builder = builder
        self.provider = provider

    async def compact(
        self, messages: list[ContextMessage], scope: HistoryCompactionScope
    ) -> list[ContextMessage]:
        """Only successful summaries advance the persisted history watermark."""
        previous, through = await self._load_checkpoint(scope)
        pending = [
            message
            for message in messages
            if not through
            or str((message.metadata or {}).get(HISTORY_TIMESTAMP, "")) > through
        ]
        existing = [summary_message(previous)] if previous else []
        over_window = len(pending) > scope.max_messages
        over_tokens = (
            self.builder._estimate_tokens([*existing, *pending]) > self._threshold
        )
        if over_window or over_tokens:
            compacted = await self._compact_pending(pending, previous, scope)
            if compacted is not None:
                return without_history_timestamps(compacted)
        # Keep the last successful summary outside the count window. A failed
        # update neither erases it nor changes which records it covers.
        return without_history_timestamps(
            [*existing, *truncate_messages_to_window(pending, scope.max_messages)]
        )

    @property
    def _threshold(self) -> int:
        return min(
            self.builder.budget.compaction_history_tokens,
            self.builder.budget.soft_token_limit,
        )

    async def _load_checkpoint(self, scope: HistoryCompactionScope) -> tuple[str, str]:
        try:
            state = (await self.memory.get_session_meta(scope.session_id)).get(
                _STATE_KEY
            )
            if not isinstance(state, dict):
                return "", ""
            if (
                state.get("mode") != scope.mode
                or state.get("scope_start") != scope.scope_start
            ):
                return "", ""
            summary, through = state.get("summary"), state.get("through")
            if not isinstance(summary, str) or not isinstance(through, str):
                return "", ""
            if (
                not through
                or not 0
                < len(summary)
                <= self.builder.budget.compaction_summary_max_chars
            ):
                return "", ""
            parsed = datetime.fromisoformat(through)
            if parsed.tzinfo is None:
                return "", ""
            return summary, parsed.astimezone(UTC).isoformat()
        except Exception as exc:
            logger.warning(
                "context.checkpoint_read_failed", error_type=type(exc).__name__
            )
            return "", ""

    def _split_pending(
        self, pending: list[ContextMessage], max_messages: int
    ) -> tuple[list[ContextMessage], list[ContextMessage]]:
        older, recent = split_history(
            pending, self.builder.budget.compaction_keep_recent_turns
        )
        while recent and (
            len(recent) > max_messages
            or self.builder._estimate_tokens(recent) > self._threshold * 0.6
        ):
            # Leave headroom for the summary and live request, moving only
            # complete user turns (including all paired tool messages).
            boundary = next(
                (
                    i
                    for i, message in enumerate(recent)
                    if i > 0 and message.role == "user"
                ),
                len(recent),
            )
            # A replay run can contain several user turns (e.g. steering).
            # Its timestamp is one checkpoint unit, so retain or summarize
            # the whole run rather than discarding its retained tail later.
            while boundary < len(recent):
                before = (recent[boundary - 1].metadata or {}).get(HISTORY_TIMESTAMP)
                after = (recent[boundary].metadata or {}).get(HISTORY_TIMESTAMP)
                if not before or before != after:
                    break
                boundary += 1
            older.extend(recent[:boundary])
            recent = recent[boundary:]
        return older, recent

    async def _compact_pending(
        self,
        pending: list[ContextMessage],
        previous: str,
        scope: HistoryCompactionScope,
    ) -> list[ContextMessage] | None:
        older, recent = self._split_pending(pending, scope.max_messages)
        if not older:
            return None
        summary = await ModelCompactor(self.builder).summarize(
            older,
            provider=self.provider,
            model=scope.model,
            previous_summary=previous,
            stop_event=scope.stop_event,
        )
        through = (older[-1].metadata or {}).get(HISTORY_TIMESTAMP)
        if summary is None or not isinstance(through, str):
            return None
        if scope.stop_event is not None and scope.stop_event.is_set():
            return None
        if not await self._save_checkpoint(scope, summary, through):
            return None
        return [summary_message(summary), *recent]

    async def _save_checkpoint(
        self, scope: HistoryCompactionScope, summary: str, through: str
    ) -> bool:
        try:
            await self.memory.update_session_meta(
                scope.session_id,
                {
                    _STATE_KEY: {
                        "mode": scope.mode,
                        "scope_start": scope.scope_start,
                        "through": through,
                        "summary": summary,
                    }
                },
            )
            return True
        except Exception as exc:
            logger.warning(
                "context.checkpoint_write_failed", error_type=type(exc).__name__
            )
            return False
