from __future__ import annotations

import pytest

from nahida_bot.voice import ReflexPolicyConfig, RealtimeVoiceSession


def _ids(*values: str):
    items = iter(values)
    return lambda: next(items)


def _session() -> RealtimeVoiceSession:
    return RealtimeVoiceSession(
        "voice-1",
        episode_id="episode-1",
        reflex_config=ReflexPolicyConfig(
            thinking_delay_ms=300,
            cooldown_ms=5_000,
            delivery_ttl_ms=500,
        ),
        turn_id_factory=_ids("turn-1", "turn-2"),
        reflex_id_factory=_ids("reflex-1", "reflex-2"),
    )


def test_session_composes_final_asr_reflex_and_formal_audio() -> None:
    session = _session()
    started = session.speech_started()
    assert started.turn is not None
    assert started.turn.turn_id == "turn-1"

    assert session.speech_stopped("turn-1") is True
    effects = session.finalize_transcript(
        "turn-1", "帮我查一下", now_ms=1_000, reflex_cue="checking"
    )
    assert effects is not None
    assert effects.reflex == ()
    assert session.turn_state == "thinking"

    assert session.pop_due_reflex(now_ms=1_299) is None
    play = session.pop_due_reflex(now_ms=1_300)
    assert play is not None
    assert play.type == "play"
    assert play.turn_id == "turn-1"
    assert play.cue == "checking"

    formal = session.assistant_audio_started("turn-1")
    assert formal is not None
    assert len(formal.reflex) == 1
    cancel = formal.reflex[0]
    assert cancel.type == "cancel"
    assert cancel.command_id == play.command_id
    assert cancel.reason == "formal_audio"
    assert session.turn_state == "speaking"

    completed = session.complete_turn("turn-1")
    assert completed is not None
    assert completed.reflex == ()
    assert session.turn_state == "idle"


def test_barge_in_while_thinking_cancels_reflex_agent_and_tts() -> None:
    session = _session()
    session.speech_started()
    session.finalize_transcript("turn-1", "第一个问题", now_ms=0)
    play = session.pop_due_reflex(now_ms=300)
    assert play is not None

    effects = session.speech_started()
    assert effects.turn is not None
    assert effects.turn.turn_id == "turn-2"
    assert effects.turn.interrupted_turn_id == "turn-1"
    assert effects.turn.cancel_agent is True
    assert effects.turn.cancel_tts is True
    assert effects.turn.clear_playback is False
    assert len(effects.reflex) == 1
    assert effects.reflex[0].type == "cancel"
    assert effects.reflex[0].reason == "user_speech"


def test_stale_asr_and_audio_events_cannot_schedule_or_cancel_current_turn() -> None:
    session = _session()
    session.speech_started()
    assert session.finalize_transcript("stale", "旧结果", now_ms=0) is None
    assert session.pop_due_reflex(now_ms=10_000) is None
    assert session.assistant_audio_started("stale") is None
    assert session.turn_state == "listening"


def test_close_cancels_both_subsystems_and_is_idempotent() -> None:
    session = _session()
    session.speech_started()
    session.finalize_transcript("turn-1", "问题", now_ms=0)
    play = session.pop_due_reflex(now_ms=300)
    assert play is not None

    effects = session.close()
    assert effects.turn is not None
    assert effects.turn.cancel_agent is True
    assert len(effects.reflex) == 1
    assert effects.reflex[0].type == "cancel"
    assert effects.reflex[0].reason == "session_closed"
    assert session.close().turn is None

    with pytest.raises(RuntimeError, match="closed"):
        session.speech_started()
