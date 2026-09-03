from __future__ import annotations

import pytest

from nahida_bot.voice import ReflexCoordinator, ReflexPolicyConfig


def _ids(*values: str):
    items = iter(values)
    return lambda: next(items)


def test_reflex_is_issued_only_after_server_delay_with_expiry() -> None:
    reflex = ReflexCoordinator(
        "voice-1",
        config=ReflexPolicyConfig(
            thinking_delay_ms=300,
            cooldown_ms=5_000,
            delivery_ttl_ms=400,
        ),
        id_factory=_ids("command-1"),
    )

    assert reflex.schedule_thinking("turn-1", now_ms=1_000) is None
    assert reflex.pending_due_at_ms == 1_300
    assert reflex.pop_due(now_ms=1_299) is None

    command = reflex.pop_due(now_ms=1_300)
    assert command is not None
    assert command.type == "play"
    assert command.command_id == "command-1"
    assert command.session_id == "voice-1"
    assert command.turn_id == "turn-1"
    assert command.cue == "thinking"
    assert command.expires_at_ms == 1_700


def test_pending_reflex_can_be_cancelled_without_transport_command() -> None:
    reflex = ReflexCoordinator("voice-1")
    reflex.schedule_thinking("turn-1", now_ms=1_000)

    assert reflex.user_speech_started() is None
    assert reflex.pending_due_at_ms is None
    assert reflex.pop_due(now_ms=10_000) is None


def test_issued_reflex_gets_cancel_command_on_formal_audio() -> None:
    reflex = ReflexCoordinator(
        "voice-1",
        config=ReflexPolicyConfig(thinking_delay_ms=0),
        id_factory=_ids("command-1"),
    )
    reflex.schedule_thinking("turn-1", now_ms=0, cue="checking")
    play = reflex.pop_due(now_ms=0)
    assert play is not None

    cancel = reflex.formal_audio_started()
    assert cancel is not None
    assert cancel.type == "cancel"
    assert cancel.command_id == play.command_id
    assert cancel.turn_id == "turn-1"
    assert cancel.reason == "formal_audio"
    assert reflex.active_command is None


def test_new_turn_cancels_active_cue_and_respects_global_cooldown() -> None:
    reflex = ReflexCoordinator(
        "voice-1",
        config=ReflexPolicyConfig(thinking_delay_ms=100, cooldown_ms=5_000),
        id_factory=_ids("command-1", "command-2"),
    )
    reflex.schedule_thinking("turn-1", now_ms=1_000)
    first = reflex.pop_due(now_ms=1_100)
    assert first is not None

    cancel = reflex.schedule_thinking("turn-2", now_ms=1_200)
    assert cancel is not None
    assert cancel.reason == "thinking_restarted"
    assert cancel.turn_id == "turn-1"
    assert reflex.pending_due_at_ms == 6_100
    assert reflex.pop_due(now_ms=6_099) is None
    second = reflex.pop_due(now_ms=6_100)
    assert second is not None
    assert second.turn_id == "turn-2"


def test_stale_terminal_events_do_not_cancel_current_turn() -> None:
    reflex = ReflexCoordinator(
        "voice-1",
        config=ReflexPolicyConfig(thinking_delay_ms=0),
        id_factory=_ids("command-1"),
    )
    reflex.schedule_thinking("turn-2", now_ms=0)
    play = reflex.pop_due(now_ms=0)
    assert play is not None

    assert reflex.turn_finished("turn-1") is None
    assert reflex.active_command == play
    assert reflex.cue_finished("wrong-command") is False
    assert reflex.cue_finished(play.command_id) is True
    assert reflex.active_command is None


def test_close_cancels_active_and_rejects_future_schedules() -> None:
    reflex = ReflexCoordinator(
        "voice-1",
        config=ReflexPolicyConfig(thinking_delay_ms=0),
        id_factory=_ids("command-1"),
    )
    reflex.schedule_thinking("turn-1", now_ms=0)
    assert reflex.pop_due(now_ms=0) is not None

    cancel = reflex.close()
    assert cancel is not None
    assert cancel.reason == "session_closed"
    assert reflex.close() is None
    with pytest.raises(RuntimeError, match="closed"):
        reflex.schedule_thinking("turn-2", now_ms=1)
    with pytest.raises(RuntimeError, match="closed"):
        reflex.user_speech_started()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("thinking_delay_ms", -1),
        ("cooldown_ms", -1),
        ("delivery_ttl_ms", 0),
    ],
)
def test_reflex_policy_rejects_invalid_timing(field: str, value: int) -> None:
    values = {
        "thinking_delay_ms": 300,
        "cooldown_ms": 5_000,
        "delivery_ttl_ms": 500,
    }
    values[field] = value
    with pytest.raises(ValueError):
        ReflexPolicyConfig(**values)
