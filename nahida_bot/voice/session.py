"""Platform-neutral composition root for realtime voice control state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from nahida_bot.voice.reflex import (
    ReflexCommand,
    ReflexCoordinator,
    ReflexCue,
    ReflexPolicyConfig,
)
from nahida_bot.voice.turns import (
    TurnDirective,
    VoiceTurnCoordinator,
    VoiceTurnSnapshot,
    VoiceTurnState,
)


@dataclass(slots=True, frozen=True)
class VoiceSessionEffects:
    """Commands produced by one atomic voice-session state transition."""

    turn: TurnDirective | None = None
    reflex: tuple[ReflexCommand, ...] = ()


class RealtimeVoiceSession:
    """Keep turn cancellation and reflex policy consistent across platforms.

    Audio I/O, task launching, timers, and networking stay outside this class.
    Desktop and Discord runtimes feed the same normalized lifecycle events and
    execute the returned effects through their transport-specific adapters.
    """

    def __init__(
        self,
        session_id: str,
        *,
        episode_id: str = "",
        reflex_config: ReflexPolicyConfig | None = None,
        turn_id_factory: Callable[[], str] | None = None,
        reflex_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._turns = VoiceTurnCoordinator(
            session_id,
            episode_id=episode_id,
            id_factory=turn_id_factory,
        )
        self._reflexes = ReflexCoordinator(
            session_id,
            config=reflex_config,
            id_factory=reflex_id_factory,
        )
        self._closed = False

    @property
    def turn_state(self) -> VoiceTurnState:
        return self._turns.state

    @property
    def current_turn(self) -> VoiceTurnSnapshot | None:
        return self._turns.current

    def speech_started(self) -> VoiceSessionEffects:
        """Apply immediate barge-in cleanup and start/resume user listening."""

        self._ensure_open()
        reflex = self._reflexes.user_speech_started()
        turn = self._turns.speech_started()
        return VoiceSessionEffects(turn=turn, reflex=self._commands(reflex))

    def update_partial(self, turn_id: str, text: str) -> bool:
        self._ensure_open()
        return self._turns.update_partial(turn_id, text)

    def speech_stopped(self, turn_id: str) -> bool:
        self._ensure_open()
        return self._turns.speech_stopped(turn_id)

    def finalize_transcript(
        self,
        turn_id: str,
        text: str,
        *,
        now_ms: int,
        reflex_cue: ReflexCue = "thinking",
    ) -> VoiceSessionEffects | None:
        """Commit final ASR and schedule a server-authorized thinking cue."""

        self._ensure_open()
        if not self._turns.finalize_transcript(turn_id, text):
            return None
        cancel = self._reflexes.schedule_thinking(
            turn_id,
            now_ms=now_ms,
            cue=reflex_cue,
        )
        return VoiceSessionEffects(reflex=self._commands(cancel))

    def pop_due_reflex(self, *, now_ms: int) -> ReflexCommand | None:
        self._ensure_open()
        return self._reflexes.pop_due(now_ms=now_ms)

    def assistant_audio_started(self, turn_id: str) -> VoiceSessionEffects | None:
        """Enter formal playback and revoke any reflex cue first."""

        self._ensure_open()
        if not self._turns.assistant_audio_started(turn_id):
            return None
        cancel = self._reflexes.formal_audio_started()
        return VoiceSessionEffects(reflex=self._commands(cancel))

    def complete_turn(self, turn_id: str) -> VoiceSessionEffects | None:
        self._ensure_open()
        if not self._turns.complete(turn_id):
            return None
        cancel = self._reflexes.turn_finished(turn_id)
        return VoiceSessionEffects(reflex=self._commands(cancel))

    def reflex_finished(self, command_id: str) -> bool:
        self._ensure_open()
        return self._reflexes.cue_finished(command_id)

    def close(self) -> VoiceSessionEffects:
        if self._closed:
            return VoiceSessionEffects()
        reflex = self._reflexes.close()
        turn = self._turns.close()
        self._closed = True
        return VoiceSessionEffects(turn=turn, reflex=self._commands(reflex))

    @staticmethod
    def _commands(command: ReflexCommand | None) -> tuple[ReflexCommand, ...]:
        return (command,) if command is not None else ()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("realtime voice session is closed")


__all__ = ["RealtimeVoiceSession", "VoiceSessionEffects"]
