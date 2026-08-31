"""Conversation engagement state machine per group session."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from nahida_bot.plugins.base import InboundMessage
from nahida_bot.plugins.conversation_joiner.config import EngagementConfig

EngagementState = Literal["observing", "joining", "engaged", "cooling"]


@dataclass(slots=True)
class GroupJoinerState:
    """Per-group engagement state machine."""

    chat_key: str
    state: EngagementState = "observing"
    episode_id: str = ""
    topic_started_at: float = 0.0
    state_updated_at: float = 0.0
    last_decision_at: float = 0.0
    last_triggered_at: float = 0.0
    last_agent_reply_at: float = 0.0
    last_observed_at: float = 0.0
    triggered_timestamps: list[float] = field(default_factory=list)
    observation_timestamps: list[float] = field(default_factory=list)
    agent_reply_timestamps: list[float] = field(default_factory=list)
    low_value_strikes: int = 0
    engagement_score: float = 0.5
    score_updated_at: float = 0.0


@dataclass(slots=True)
class ObservedMessageBatch:
    """Buffer for messages collected during engaged state."""

    chat_key: str
    started_at: float
    messages: list[Any] = field(default_factory=list)  # list[InboundMessage]
    total_chars: int = 0
    gate_failures: int = 0


@dataclass(slots=True, frozen=True)
class PresenceSnapshot:
    """Recent human/bot participation used by the continuation gate."""

    observed_messages: int
    agent_replies: int
    bot_share: float


class EngagementStateMachine:
    """Manages per-group engagement states, transitions, and batching."""

    def __init__(self, logger: Any) -> None:
        self._states: dict[str, GroupJoinerState] = {}
        self._batches: dict[str, ObservedMessageBatch] = {}
        self._window_timers: dict[str, Any] = {}  # chat_key -> asyncio.TimerHandle
        self._logger = logger

    # ------------------------------------------------------------------
    # State access
    # ------------------------------------------------------------------

    def get_state(self, chat_key: str) -> GroupJoinerState:
        """Return the state for *chat_key*, creating a default if new."""
        if chat_key not in self._states:
            now = time.monotonic()
            self._states[chat_key] = GroupJoinerState(
                chat_key=chat_key,
                state_updated_at=now,
                last_observed_at=now,
                score_updated_at=now,
            )
        return self._states[chat_key]

    def get_batch(self, chat_key: str) -> ObservedMessageBatch | None:
        """Return the current batch for an engaged group, if any."""
        return self._batches.get(chat_key)

    def remove_state(self, chat_key: str) -> None:
        """Clean up state and batch for *chat_key*."""
        self._cancel_window_timer(chat_key)
        self._states.pop(chat_key, None)
        self._batches.pop(chat_key, None)

    # ------------------------------------------------------------------
    # Transitions
    # ------------------------------------------------------------------

    def transition_to_joining(self, chat_key: str, now: float) -> None:
        """observing -> joining.  Called when join_gate passes."""
        state = self.get_state(chat_key)
        old = state.state
        state.state = "joining"
        state.episode_id = uuid4().hex
        state.state_updated_at = now
        state.topic_started_at = now
        self._log_transition(chat_key, old, "joining")

    def transition_to_engaged(
        self,
        chat_key: str,
        now: float,
        *,
        reply_window_seconds: float = 300.0,
    ) -> None:
        """joining -> engaged.  Called when agent sends a real reply."""
        state = self.get_state(chat_key)
        old = state.state
        state.state = "engaged"
        state.state_updated_at = now
        # Direct mentions can enter engagement without passing through
        # ``joining``. Start a new episode only from observing; cooling and
        # already-engaged direct replies remain part of the current episode.
        if old == "observing" or not state.episode_id:
            state.episode_id = uuid4().hex
        if old == "observing" or state.topic_started_at <= 0:
            state.topic_started_at = now
        self.record_agent_reply(
            chat_key,
            now,
            max_age_seconds=reply_window_seconds,
        )
        state.engagement_score = min(1.0, state.engagement_score + 0.1)
        state.score_updated_at = now
        state.low_value_strikes = 0
        # A direct reply while already engaged is part of the same episode.
        # Preserve messages that arrived before the reply instead of silently
        # replacing the in-flight batch.
        if old not in ("engaged", "cooling") or chat_key not in self._batches:
            self._batches[chat_key] = ObservedMessageBatch(
                chat_key=chat_key,
                started_at=now,
            )
        self._log_transition(chat_key, old, "engaged")

    def transition_to_observing(
        self,
        chat_key: str,
        now: float,
        *,
        reason: str,
    ) -> None:
        """Any state -> observing.  Cancels timers, clears batch."""
        state = self.get_state(chat_key)
        old = state.state
        state.state = "observing"
        state.episode_id = ""
        state.state_updated_at = now
        state.low_value_strikes = 0
        state.score_updated_at = now
        self._cancel_window_timer(chat_key)
        self._batches.pop(chat_key, None)
        self._log_transition(chat_key, old, "observing", reason=reason)

    def transition_to_cooling(self, chat_key: str, now: float) -> None:
        """engaged -> cooling.  Called after a proactive response."""
        state = self.get_state(chat_key)
        old = state.state
        state.state = "cooling"
        state.state_updated_at = now
        state.last_triggered_at = now
        self._log_transition(chat_key, old, "cooling")

    def try_transition_from_cooling(
        self,
        chat_key: str,
        now: float,
        cooldown_seconds: float,
    ) -> bool:
        """Check whether the cooling period has elapsed and transition back
        to *engaged* if so.  Returns ``True`` when the transition happened."""
        state = self.get_state(chat_key)
        if state.state != "cooling":
            return False
        if now - state.state_updated_at < cooldown_seconds:
            return False
        old = state.state
        state.state = "engaged"
        state.state_updated_at = now
        self._log_transition(chat_key, old, "engaged")
        return True

    # ------------------------------------------------------------------
    # Batch
    # ------------------------------------------------------------------

    def append_to_batch(
        self,
        chat_key: str,
        message: InboundMessage,
        cfg: EngagementConfig,
        now: float,
    ) -> bool:
        """Add *message* to the engagement buffer.

        Returns ``True`` when the batch is now full (hit ``max_messages``
        or ``max_chars``).  Creates a new batch if one does not exist.
        """
        batch = self._batches.get(chat_key)
        if batch is None:
            batch = ObservedMessageBatch(chat_key=chat_key, started_at=now)
            self._batches[chat_key] = batch

        text_len = len(message.text) if message.text else 0
        batch.messages.append(message)
        batch.total_chars += text_len

        batching = cfg.batching
        while len(batch.messages) > batching.max_messages:
            dropped = batch.messages.pop(0)
            batch.total_chars -= len(dropped.text) if dropped.text else 0
            batch.started_at = now
        while batch.total_chars > batching.max_chars and len(batch.messages) > 1:
            dropped = batch.messages.pop(0)
            batch.total_chars -= len(dropped.text) if dropped.text else 0
            batch.started_at = now

        return (
            len(batch.messages) >= batching.max_messages
            or batch.total_chars >= batching.max_chars
        )

    def clear_batch(self, chat_key: str) -> None:
        """Clear the batch after it has been flushed."""
        batch = self._batches.pop(chat_key, None)
        # Re-create an empty batch so the engaged window keeps collecting.
        if batch is not None:
            self._batches[chat_key] = ObservedMessageBatch(
                chat_key=chat_key,
                started_at=time.monotonic(),
            )
        self._cancel_window_timer(chat_key)

    def record_batch_gate_failure(self, chat_key: str) -> int:
        """Increment and return the current batch's gate failure count."""
        batch = self._batches.get(chat_key)
        if batch is None:
            return 0
        batch.gate_failures += 1
        return batch.gate_failures

    # ------------------------------------------------------------------
    # Window timer
    # ------------------------------------------------------------------

    def schedule_window_flush(
        self,
        chat_key: str,
        delay_seconds: float,
        callback: Any,
    ) -> None:
        """Schedule a one-shot asyncio timer to flush the batch window.

        *callback* is a callable (typically ``lambda: asyncio.ensure_future(...)``)
        that will be invoked after *delay_seconds*.  Cancels any existing timer
        for this *chat_key* first.
        """
        self._cancel_window_timer(chat_key)
        import asyncio

        loop = asyncio.get_running_loop()
        handle = loop.call_later(delay_seconds, callback)
        self._window_timers[chat_key] = handle

    def has_window_timer(self, chat_key: str) -> bool:
        """Return True if a window timer is already scheduled for this chat."""
        return chat_key in self._window_timers

    def mark_window_timer_fired(self, chat_key: str) -> None:
        """Forget a timer handle after its callback starts running."""
        self._window_timers.pop(chat_key, None)

    def cancel_all_timers(self) -> None:
        """Cancel all scheduled window timers.  Hook for on_unload."""
        for handle in self._window_timers.values():
            handle.cancel()
        self._window_timers.clear()

    # ------------------------------------------------------------------
    # Exit conditions
    # ------------------------------------------------------------------

    def check_exit_conditions(
        self,
        chat_key: str,
        now: float,
        cfg: EngagementConfig,
    ) -> str | None:
        """Check all exit signals for an engaged/cooling group.

        Returns a reason string when the group should exit to *observing*,
        or ``None`` when it should remain.
        """
        state = self.get_state(chat_key)
        if state.state not in ("engaged", "cooling"):
            return None

        exit_cfg = cfg.exit_gate

        # 1. Absolute max duration
        if (
            state.topic_started_at > 0
            and now - state.topic_started_at >= cfg.max_engaged_seconds
        ):
            return "max_engaged_seconds"

        # 2. Topic TTL
        if (
            state.topic_started_at > 0
            and now - state.topic_started_at >= cfg.join_state_ttl_seconds
        ):
            return "ttl_expired"

        # 3. Idle timeout (no recent observed messages)
        if (
            state.last_observed_at > 0
            and now - state.last_observed_at >= cfg.idle_exit_seconds
        ):
            return "idle_timeout"

        # 4. Activity window — low observed message density
        if exit_cfg.enabled and exit_cfg.activity_window_seconds > 0:
            window_start = now - exit_cfg.activity_window_seconds
            # Trim old observation timestamps and count recent ones.
            obs = state.observation_timestamps
            while obs and obs[0] < window_start:
                obs.pop(0)
            recent_count = len(obs)
            if (
                recent_count < exit_cfg.min_messages_per_window
                and state.topic_started_at > 0
            ):
                elapsed = now - state.topic_started_at
                if elapsed >= exit_cfg.activity_window_seconds:
                    return "low_activity"

        # 5. Repeated confident decisions to stay silent.  Treating strikes as
        # an independent exit signal mirrors a person naturally dropping out
        # after several turns where they had nothing useful to add.
        if exit_cfg.enabled and state.low_value_strikes >= exit_cfg.low_value_strikes:
            return "low_value_strikes"
        if (
            exit_cfg.enabled
            and state.low_value_strikes > 0
            and state.engagement_score < cfg.engagement_score_exit_threshold
        ):
            return "low_engagement_score"

        return None

    def record_observation(
        self,
        chat_key: str,
        now: float,
        *,
        max_age_seconds: float = 0.0,
    ) -> None:
        """Update ``last_observed_at`` and record the timestamp for activity tracking."""
        state = self.get_state(chat_key)
        state.last_observed_at = now
        state.observation_timestamps.append(now)
        if max_age_seconds > 0:
            cutoff = now - max_age_seconds
            obs = state.observation_timestamps
            while obs and obs[0] < cutoff:
                obs.pop(0)

    def record_agent_reply(
        self,
        chat_key: str,
        now: float,
        *,
        max_age_seconds: float,
    ) -> None:
        """Record a visible bot reply for recent-presence accounting."""
        state = self.get_state(chat_key)
        state.last_agent_reply_at = now
        state.agent_reply_timestamps.append(now)
        if max_age_seconds <= 0:
            return
        cutoff = now - max_age_seconds
        replies = state.agent_reply_timestamps
        while replies and replies[0] < cutoff:
            replies.pop(0)

    def presence_snapshot(
        self,
        chat_key: str,
        now: float,
        window_seconds: float,
    ) -> PresenceSnapshot:
        """Return recent human/bot participation after trimming stale samples."""
        state = self.get_state(chat_key)
        cutoff = now - window_seconds
        observations = state.observation_timestamps
        while observations and observations[0] < cutoff:
            observations.pop(0)
        replies = state.agent_reply_timestamps
        while replies and replies[0] < cutoff:
            replies.pop(0)
        observed_count = len(observations)
        reply_count = len(replies)
        total = observed_count + reply_count
        return PresenceSnapshot(
            observed_messages=observed_count,
            agent_replies=reply_count,
            bot_share=reply_count / total if total else 0.0,
        )

    def update_engagement_score(
        self,
        chat_key: str,
        signal: float,
        alpha: float,
        now: float | None = None,
    ) -> None:
        """EWMA: ``score = score * (1 - alpha) + signal * alpha``."""
        state = self.get_state(chat_key)
        state.engagement_score = state.engagement_score * (1 - alpha) + signal * alpha
        state.score_updated_at = time.monotonic() if now is None else now

    def decay_engagement_score(
        self,
        chat_key: str,
        now: float,
        cfg: EngagementConfig,
    ) -> None:
        """Apply lazy exponential time decay to the engagement score."""
        state = self.get_state(chat_key)
        if state.state not in ("engaged", "cooling"):
            state.score_updated_at = now
            return

        half_life = cfg.score_decay_half_life_seconds
        if half_life <= 0:
            state.score_updated_at = now
            return

        previous = state.score_updated_at or state.state_updated_at or now
        elapsed = max(0.0, now - previous)
        if elapsed <= 0:
            return

        floor = min(max(cfg.score_decay_floor, 0.0), state.engagement_score)
        retained = 0.5 ** (elapsed / half_life)
        state.engagement_score = floor + (state.engagement_score - floor) * retained
        state.score_updated_at = now

    def increment_low_value_strike(self, chat_key: str) -> None:
        """Increment the low-value strikes counter."""
        state = self.get_state(chat_key)
        state.low_value_strikes += 1

    def reset_low_value_strikes(self, chat_key: str) -> None:
        """Reset strikes after a successful engagement action."""
        state = self.get_state(chat_key)
        state.low_value_strikes = 0

    # ------------------------------------------------------------------
    # Serialization hooks (for future plugin_data persistence)
    # ------------------------------------------------------------------

    def serialize_state(self, chat_key: str) -> dict[str, Any] | None:
        """Serialize state for plugin_data storage."""
        state = self._states.get(chat_key)
        if state is None:
            return None
        return {
            "chat_key": state.chat_key,
            "state": state.state,
            "episode_id": state.episode_id,
            "topic_started_at": state.topic_started_at,
            "state_updated_at": state.state_updated_at,
            "last_decision_at": state.last_decision_at,
            "last_triggered_at": state.last_triggered_at,
            "last_agent_reply_at": state.last_agent_reply_at,
            "last_observed_at": state.last_observed_at,
            "triggered_timestamps": list(state.triggered_timestamps),
            "observation_timestamps": list(state.observation_timestamps),
            "agent_reply_timestamps": list(state.agent_reply_timestamps),
            "low_value_strikes": state.low_value_strikes,
            "engagement_score": state.engagement_score,
            "score_updated_at": state.score_updated_at,
        }

    def deserialize_state(self, chat_key: str, data: dict[str, Any]) -> None:
        """Restore state from plugin_data."""
        state = GroupJoinerState(
            chat_key=data.get("chat_key", chat_key),
            state=data.get("state", "observing"),
            episode_id=str(data.get("episode_id", "") or ""),
            topic_started_at=data.get("topic_started_at", 0.0),
            state_updated_at=data.get("state_updated_at", 0.0),
            last_decision_at=data.get("last_decision_at", 0.0),
            last_triggered_at=data.get("last_triggered_at", 0.0),
            last_agent_reply_at=data.get("last_agent_reply_at", 0.0),
            last_observed_at=data.get("last_observed_at", 0.0),
            triggered_timestamps=data.get("triggered_timestamps", []),
            observation_timestamps=data.get("observation_timestamps", []),
            agent_reply_timestamps=data.get("agent_reply_timestamps", []),
            low_value_strikes=data.get("low_value_strikes", 0),
            engagement_score=data.get("engagement_score", 0.5),
            score_updated_at=data.get(
                "score_updated_at",
                data.get("state_updated_at", 0.0),
            ),
        )
        if state.state != "observing" and not state.episode_id:
            state.episode_id = uuid4().hex
        self._states[chat_key] = state

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _log_transition(
        self,
        chat_key: str,
        old_state: EngagementState,
        new_state: EngagementState,
        *,
        reason: str = "",
    ) -> None:
        self._logger.debug(
            "conversation_joiner.state_transition",
            chat_key=chat_key,
            old_state=old_state,
            new_state=new_state,
            episode_id=self.get_state(chat_key).episode_id,
            reason=reason,
        )

    def _cancel_window_timer(self, chat_key: str) -> None:
        handle = self._window_timers.pop(chat_key, None)
        if handle is not None:
            handle.cancel()
