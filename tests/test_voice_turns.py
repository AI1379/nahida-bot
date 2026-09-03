from __future__ import annotations

import pytest

from nahida_bot.voice import VoiceTurnCoordinator


def _ids(*values: str):
    items = iter(values)
    return lambda: next(items)


def test_voice_turn_happy_path_and_stale_result_rejection() -> None:
    coordinator = VoiceTurnCoordinator(
        "desktop:voice:primary",
        episode_id="episode-1",
        id_factory=_ids("turn-1"),
    )

    directive = coordinator.speech_started()
    assert directive.turn_id == "turn-1"
    assert coordinator.state == "listening"
    assert coordinator.update_partial("turn-1", "你好") is True
    assert coordinator.speech_stopped("turn-1") is True
    assert coordinator.state == "endpointing"
    assert coordinator.finalize_transcript("turn-1", "你好呀") is True
    assert coordinator.current is not None
    assert coordinator.current.final_text == "你好呀"
    assert coordinator.state == "thinking"
    assert coordinator.assistant_audio_started("turn-1") is True
    assert coordinator.state == "speaking"
    assert coordinator.complete("turn-1") is True
    assert coordinator.state == "idle"

    assert coordinator.update_partial("turn-1", "迟到") is False


def test_speech_resumes_same_turn_during_candidate_endpoint() -> None:
    coordinator = VoiceTurnCoordinator("s", id_factory=_ids("turn-1"))
    first = coordinator.speech_started()
    assert coordinator.speech_stopped(first.turn_id) is True

    resumed = coordinator.speech_started()
    assert resumed.turn_id == first.turn_id
    assert resumed.interrupted_turn_id == ""
    assert coordinator.state == "listening"


def test_barge_in_cancels_agent_tts_and_playback() -> None:
    coordinator = VoiceTurnCoordinator("s", id_factory=_ids("turn-1", "turn-2"))
    first = coordinator.speech_started()
    coordinator.finalize_transcript(first.turn_id, "第一个问题")
    coordinator.assistant_audio_started(first.turn_id)

    second = coordinator.speech_started()
    assert second.turn_id == "turn-2"
    assert second.interrupted_turn_id == "turn-1"
    assert second.cancel_agent is True
    assert second.cancel_tts is True
    assert second.clear_playback is True
    assert coordinator.accepts("turn-1") is False
    assert coordinator.accepts("turn-2") is True


def test_interrupt_thinking_does_not_request_playback_clear() -> None:
    coordinator = VoiceTurnCoordinator("s", id_factory=_ids("turn-1"))
    turn = coordinator.speech_started()
    coordinator.finalize_transcript(turn.turn_id, "问题")

    directive = coordinator.interrupt_current("session replaced")
    assert directive is not None
    assert directive.cancel_agent is True
    assert directive.cancel_tts is True
    assert directive.clear_playback is False
    assert coordinator.state == "interrupted"


def test_close_is_idempotent_and_rejects_new_speech() -> None:
    coordinator = VoiceTurnCoordinator("s", id_factory=_ids("turn-1"))
    turn = coordinator.speech_started()
    coordinator.finalize_transcript(turn.turn_id, "问题")

    directive = coordinator.close()
    assert directive is not None
    assert directive.cancel_agent is True
    assert coordinator.state == "closed"
    assert coordinator.close() is None
    with pytest.raises(RuntimeError, match="closed"):
        coordinator.speech_started()
