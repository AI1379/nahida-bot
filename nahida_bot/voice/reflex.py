"""Server-owned reflex policy for every realtime voice transport.

The coordinator decides *whether* and *when* a non-semantic cue may play.
Desktop, Discord, and future transports only execute expiring play/cancel
commands against preloaded audio.  Keeping the policy here gives all transports
the same cooldown, turn ownership, and cancellation semantics.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, TypeAlias
from uuid import uuid4

ReflexCue = Literal["acknowledge", "thinking", "checking"]
ReflexCancelReason = Literal[
    "user_speech",
    "formal_audio",
    "turn_finished",
    "thinking_restarted",
    "session_closed",
]


@dataclass(slots=True, frozen=True)
class ReflexPolicyConfig:
    """Timing policy shared by Desktop, Discord, and future transports."""

    thinking_delay_ms: int = 300
    cooldown_ms: int = 5_000
    delivery_ttl_ms: int = 500

    def __post_init__(self) -> None:
        if self.thinking_delay_ms < 0:
            raise ValueError("thinking_delay_ms must be non-negative")
        if self.cooldown_ms < 0:
            raise ValueError("cooldown_ms must be non-negative")
        if self.delivery_ttl_ms <= 0:
            raise ValueError("delivery_ttl_ms must be positive")


@dataclass(slots=True, frozen=True)
class ReflexPlayCommand:
    """Expiring semantic cue command sent to one voice output adapter."""

    command_id: str
    session_id: str
    turn_id: str
    cue: ReflexCue
    expires_at_ms: int
    interruptible: bool = True
    type: Literal["play"] = field(default="play", init=False)


@dataclass(slots=True, frozen=True)
class ReflexCancelCommand:
    """Cancel an already-issued reflex cue for a specific turn."""

    command_id: str
    session_id: str
    turn_id: str
    reason: ReflexCancelReason
    type: Literal["cancel"] = field(default="cancel", init=False)


ReflexCommand: TypeAlias = ReflexPlayCommand | ReflexCancelCommand


@dataclass(slots=True, frozen=True)
class _PendingReflex:
    turn_id: str
    cue: ReflexCue
    due_at_ms: int


class ReflexCoordinator:
    """Pure server-side scheduler for non-semantic latency-masking cues.

    The caller owns the actual timer and transport.  It schedules a cue when a
    turn starts thinking, calls :meth:`pop_due` at the requested deadline, and
    forwards returned commands to the active voice output adapter.
    """

    def __init__(
        self,
        session_id: str,
        *,
        config: ReflexPolicyConfig | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        clean_session_id = session_id.strip()
        if not clean_session_id:
            raise ValueError("session_id must not be empty")
        self._session_id = clean_session_id
        self._config = config or ReflexPolicyConfig()
        self._id_factory = id_factory or (lambda: uuid4().hex)
        self._pending: _PendingReflex | None = None
        self._active: ReflexPlayCommand | None = None
        self._last_issued_at_ms: int | None = None
        self._closed = False

    @property
    def pending_due_at_ms(self) -> int | None:
        return self._pending.due_at_ms if self._pending is not None else None

    @property
    def active_command(self) -> ReflexPlayCommand | None:
        return self._active

    def schedule_thinking(
        self,
        turn_id: str,
        *,
        now_ms: int,
        cue: ReflexCue = "thinking",
    ) -> ReflexCancelCommand | None:
        """Schedule a cue and return cleanup for a previous issued cue."""

        self._ensure_open()
        clean_turn_id = turn_id.strip()
        if not clean_turn_id:
            raise ValueError("turn_id must not be empty")
        if self._same_scheduled_cue(clean_turn_id, cue):
            return None

        cancel = self._cancel_current("thinking_restarted")
        cooldown_until = (
            self._last_issued_at_ms + self._config.cooldown_ms
            if self._last_issued_at_ms is not None
            else now_ms
        )
        self._pending = _PendingReflex(
            turn_id=clean_turn_id,
            cue=cue,
            due_at_ms=max(now_ms + self._config.thinking_delay_ms, cooldown_until),
        )
        return cancel

    def pop_due(self, *, now_ms: int) -> ReflexPlayCommand | None:
        """Issue the pending cue once its server-owned delay has elapsed."""

        self._ensure_open()
        pending = self._pending
        if pending is None or now_ms < pending.due_at_ms:
            return None
        command_id = self._id_factory().strip()
        if not command_id:
            raise ValueError("id_factory returned an empty command_id")
        command = ReflexPlayCommand(
            command_id=command_id,
            session_id=self._session_id,
            turn_id=pending.turn_id,
            cue=pending.cue,
            expires_at_ms=now_ms + self._config.delivery_ttl_ms,
        )
        self._pending = None
        self._active = command
        self._last_issued_at_ms = now_ms
        return command

    def user_speech_started(self) -> ReflexCancelCommand | None:
        self._ensure_open()
        return self._cancel_current("user_speech")

    def formal_audio_started(self) -> ReflexCancelCommand | None:
        self._ensure_open()
        return self._cancel_current("formal_audio")

    def turn_finished(self, turn_id: str) -> ReflexCancelCommand | None:
        """Cancel only when the terminal event still owns the scheduled turn."""

        self._ensure_open()
        if not self._owns_turn(turn_id):
            return None
        return self._cancel_current("turn_finished")

    def cue_finished(self, command_id: str) -> bool:
        """Acknowledge terminal playback feedback from a transport adapter."""

        self._ensure_open()
        if self._active is None or self._active.command_id != command_id:
            return False
        self._active = None
        return True

    def close(self) -> ReflexCancelCommand | None:
        if self._closed:
            return None
        cancel = self._cancel_current("session_closed")
        self._closed = True
        return cancel

    def _same_scheduled_cue(self, turn_id: str, cue: ReflexCue) -> bool:
        if self._pending is not None:
            return self._pending.turn_id == turn_id and self._pending.cue == cue
        if self._active is not None:
            return self._active.turn_id == turn_id and self._active.cue == cue
        return False

    def _owns_turn(self, turn_id: str) -> bool:
        clean_turn_id = turn_id.strip()
        if self._pending is not None and self._pending.turn_id == clean_turn_id:
            return True
        return self._active is not None and self._active.turn_id == clean_turn_id

    def _cancel_current(self, reason: ReflexCancelReason) -> ReflexCancelCommand | None:
        self._pending = None
        active = self._active
        self._active = None
        if active is None:
            return None
        return ReflexCancelCommand(
            command_id=active.command_id,
            session_id=self._session_id,
            turn_id=active.turn_id,
            reason=reason,
        )

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("reflex coordinator is closed")


__all__ = [
    "ReflexCancelCommand",
    "ReflexCancelReason",
    "ReflexCommand",
    "ReflexCoordinator",
    "ReflexCue",
    "ReflexPlayCommand",
    "ReflexPolicyConfig",
]
