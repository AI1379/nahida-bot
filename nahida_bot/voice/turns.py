"""Pure state machine for one realtime voice session's active media turn."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal
from uuid import uuid4

VoiceTurnState = Literal[
    "idle",
    "listening",
    "endpointing",
    "thinking",
    "speaking",
    "interrupted",
    "closed",
]


@dataclass(slots=True, frozen=True)
class VoiceTurnSnapshot:
    """Immutable view of the current turn generation."""

    session_id: str
    episode_id: str
    turn_id: str
    state: VoiceTurnState
    partial_text: str = ""
    final_text: str = ""
    interruption_reason: str = ""


@dataclass(slots=True, frozen=True)
class TurnDirective:
    """Side effects that the runtime must apply after a state transition."""

    turn_id: str
    interrupted_turn_id: str = ""
    cancel_agent: bool = False
    cancel_tts: bool = False
    clear_playback: bool = False


class VoiceTurnCoordinator:
    """Own the active ``turn_id`` and reject stale asynchronous results.

    This class deliberately does not perform audio I/O or launch tasks.  It
    returns explicit directives so the realtime runtime can cancel Agent/TTS
    work and clear platform playback without hiding side effects inside the
    state machine.
    """

    def __init__(
        self,
        session_id: str,
        *,
        episode_id: str = "",
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        clean_session_id = session_id.strip()
        if not clean_session_id:
            raise ValueError("session_id must not be empty")
        self._session_id = clean_session_id
        self._episode_id = episode_id.strip()
        self._id_factory = id_factory or (lambda: uuid4().hex)
        self._current: VoiceTurnSnapshot | None = None
        self._closed = False

    @property
    def current(self) -> VoiceTurnSnapshot | None:
        return self._current

    @property
    def state(self) -> VoiceTurnState:
        if self._closed:
            return "closed"
        return self._current.state if self._current is not None else "idle"

    def accepts(self, turn_id: str) -> bool:
        """Return whether an async result still belongs to the active turn."""

        return (
            not self._closed
            and self._current is not None
            and self._current.turn_id == turn_id
            and self._current.state not in {"interrupted", "closed"}
        )

    def speech_started(self) -> TurnDirective:
        """Start listening or interrupt current thinking/playback."""

        self._ensure_open()
        current = self._current
        if current is not None and current.state == "listening":
            return TurnDirective(turn_id=current.turn_id)
        if current is not None and current.state == "endpointing":
            self._current = replace(current, state="listening")
            return TurnDirective(turn_id=current.turn_id)

        cleanup: TurnDirective | None = None
        if current is not None and current.state in {"thinking", "speaking"}:
            cleanup = self._cleanup_directive(current)

        turn_id = self._id_factory()
        if not turn_id:
            raise ValueError("id_factory returned an empty turn_id")
        self._current = VoiceTurnSnapshot(
            session_id=self._session_id,
            episode_id=self._episode_id,
            turn_id=turn_id,
            state="listening",
        )
        if cleanup is None:
            return TurnDirective(turn_id=turn_id)
        return replace(cleanup, turn_id=turn_id)

    def update_partial(self, turn_id: str, text: str) -> bool:
        """Apply a partial ASR revision if it is still current."""

        if not self.accepts(turn_id) or self._current is None:
            return False
        if self._current.state not in {"listening", "endpointing"}:
            return False
        self._current = replace(self._current, partial_text=text)
        return True

    def speech_stopped(self, turn_id: str) -> bool:
        """Mark a candidate endpoint while semantic/final ASR catches up."""

        if not self.accepts(turn_id) or self._current is None:
            return False
        if self._current.state != "listening":
            return False
        self._current = replace(self._current, state="endpointing")
        return True

    def finalize_transcript(self, turn_id: str, text: str) -> bool:
        """Commit user text and transfer ownership to the Agent."""

        if not self.accepts(turn_id) or self._current is None:
            return False
        if self._current.state not in {"listening", "endpointing"}:
            return False
        self._current = replace(
            self._current,
            state="thinking",
            partial_text=text,
            final_text=text,
        )
        return True

    def assistant_audio_started(self, turn_id: str) -> bool:
        """Mark that formal assistant audio for the current turn is audible."""

        if not self.accepts(turn_id) or self._current is None:
            return False
        if self._current.state != "thinking":
            return False
        self._current = replace(self._current, state="speaking")
        return True

    def complete(self, turn_id: str) -> bool:
        """Finish the active turn and return the session to idle."""

        if not self.accepts(turn_id):
            return False
        self._current = None
        return True

    def interrupt_current(self, reason: str) -> TurnDirective | None:
        """Invalidate the current generation and describe required cleanup."""

        self._ensure_open()
        current = self._current
        if current is None or current.state == "interrupted":
            return None
        directive = self._cleanup_directive(current)
        self._current = replace(
            current,
            state="interrupted",
            interruption_reason=reason.strip(),
        )
        return directive

    def close(self) -> TurnDirective | None:
        """Close the session and request cleanup for any active generation."""

        if self._closed:
            return None
        current = self._current
        directive: TurnDirective | None = None
        if current is not None:
            directive = self._cleanup_directive(current)
            self._current = replace(current, state="closed")
        self._closed = True
        return directive

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("voice session is closed")

    @staticmethod
    def _cleanup_directive(current: VoiceTurnSnapshot) -> TurnDirective:
        generating = current.state in {"thinking", "speaking"}
        return TurnDirective(
            turn_id=current.turn_id,
            interrupted_turn_id=current.turn_id,
            cancel_agent=generating,
            cancel_tts=generating,
            clear_playback=current.state == "speaking",
        )


__all__ = [
    "TurnDirective",
    "VoiceTurnCoordinator",
    "VoiceTurnSnapshot",
    "VoiceTurnState",
]
