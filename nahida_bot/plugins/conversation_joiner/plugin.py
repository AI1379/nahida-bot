"""Conversation joiner plugin."""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from collections.abc import Callable, Iterable
from collections import deque
from dataclasses import dataclass
from typing import Any

from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.events import (
    MessageObserved,
    MessagePayload,
    MessageReceived,
    MessageSent,
    PokeEvent,
    PokePayload,
)
from nahida_bot.plugins.base import (
    AttentionFrame,
    ChatContext,
    InboundMessage,
    MessageContext,
    Plugin,
    SenderContext,
)
from nahida_bot.plugins.conversation_joiner.config import (
    EffectiveJoinerConfig,
    EngagementConfig,
    effective_group_config,
    parse_conversation_joiner_config,
)
from nahida_bot.plugins.conversation_joiner.state import (
    EngagementStateMachine,
)


@dataclass(slots=True, frozen=True)
class _ContextEntry:
    sender: str
    text: str
    timestamp: float
    message_id: str
    message: InboundMessage


@dataclass(slots=True, frozen=True)
class _SecretaryDecision:
    should_join: bool
    confidence: float
    reason: str
    entry_style: str = ""
    focus: str = ""
    reply_mode: str = ""
    reply_anchor_message_id: str = ""


@dataclass(slots=True, frozen=True)
class _PersonaContextCache:
    text: str
    loaded_at: float


@dataclass(slots=True, frozen=True)
class _PendingRequest:
    requested_at: float
    session_id: str


class ConversationJoinerPlugin(Plugin):
    """Observe group chat and request the main agent when joining makes sense."""

    def __init__(self, api: Any, manifest: Any) -> None:
        super().__init__(api, manifest)
        self._config = parse_conversation_joiner_config(self.manifest.config)
        self._contexts: dict[str, deque[_ContextEntry]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._last_decision_at: dict[str, float] = {}
        self._last_triggered_at: dict[str, float] = {}
        self._triggered_at: dict[str, list[float]] = {}
        self._persona_context_cache: _PersonaContextCache | None = None
        self._persona_context_lock = asyncio.Lock()
        self._sample_random = random.random
        self._sm: EngagementStateMachine | None = None
        self._pending_requests: dict[str, _PendingRequest] = {}
        self._monitor_tasks: dict[str, asyncio.Task[None]] = {}
        self._last_direct_engaged_message_id: dict[str, str] = {}

    async def on_load(self) -> None:
        self.api.subscribe(MessageObserved, self._on_message_observed)
        self.api.subscribe(MessageReceived, self._on_message_received)
        if self._config.prefilter.enable_poke:
            self.api.subscribe(PokeEvent, self._on_poke)
        if self._has_any_engagement_enabled():
            self._sm = EngagementStateMachine(self.api.logger)
            self.api.subscribe(MessageSent, self._on_message_sent)
        self.api.register_status_provider(
            "engagement",
            self._status_provider,
            label="Conversation Engagement",
        )
        self.api.logger.info(
            "conversation_joiner.loaded",
            enabled=self._config.enabled,
            engagement=self._config.engagement.enabled,
            group_count=len(self._config.groups),
        )

    def _has_any_engagement_enabled(self) -> bool:
        if self._config.engagement.enabled:
            return True
        return any(
            group.engagement is not None and group.engagement.enabled
            for group in self._config.groups.values()
        )

    async def on_unload(self) -> None:
        if self._sm is not None:
            self._sm.cancel_all_timers()
        await self._cancel_tasks()

    async def on_disable(self) -> None:
        if self._sm is not None:
            self._sm.cancel_all_timers()
        await self._cancel_tasks()

    async def _cancel_tasks(self) -> None:
        if not self._tasks:
            return
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._monitor_tasks.clear()

    async def _on_message_observed(self, event: MessageObserved) -> None:
        message: InboundMessage = event.payload.message
        address = _address_from_message(message)
        if address is None:
            return
        cfg = effective_group_config(self._config, address.chat_key)
        if not cfg.enabled:
            return

        task = asyncio.create_task(self._handle_observed(event, address, cfg))
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)

    async def _on_message_received(self, event: MessageReceived) -> None:
        """Observe a group message that already has a reactive agent path.

        Channels historically emit either MessageObserved or MessageReceived.
        Subscribing to both makes attention orthogonal to response: direct
        mentions and ``always``-mode messages update the same short-term
        context without asking ConversationJoiner to trigger a duplicate run.
        """
        message: InboundMessage = event.payload.message
        address = _address_from_message(message)
        if address is None:
            return
        cfg = effective_group_config(self._config, address.chat_key)
        if not cfg.enabled:
            return
        task = asyncio.create_task(
            self._remember_triggered_observation(message, address, cfg)
        )
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)

    async def _remember_triggered_observation(
        self,
        message: InboundMessage,
        address: ChatAddress,
        cfg: EffectiveJoinerConfig,
    ) -> None:
        chat_key = address.chat_key
        lock = self._locks.setdefault(chat_key, asyncio.Lock())
        async with lock:
            self._remember_context(chat_key, message, cfg)
            sm = self._sm
            if sm is None or not cfg.engagement.enabled:
                return
            now = time.monotonic()
            sm.decay_engagement_score(chat_key, now, cfg.engagement)
            sm.record_observation(
                chat_key,
                now,
                max_age_seconds=cfg.engagement.exit_gate.activity_window_seconds,
            )

    async def _on_poke(self, event: PokeEvent) -> None:
        """Receive a PokeEvent, synthesize a tagged InboundMessage, and route it
        through the same handling path as an observed message.

        Poke is a weak group-interaction signal: it is treated as an ordinary
        ambient message carrying a ``poke`` marker (in ``extra_tags`` and
        ``raw_event``), so it flows through the existing gate, batching and
        cooldown unchanged — no immediate flush, no cooldown break.
        """
        payload = event.payload
        # Friend-scene pokes are out of scope: the state machine and
        # request_agent_response are group-only.
        if payload.scene != "group":
            return
        address = payload.chat_address
        if address.target_type != "group" or not address.is_typed:
            return
        cfg = effective_group_config(self._config, address.chat_key)
        if not cfg.enabled or not cfg.prefilter.enable_poke:
            return

        message = _synthesize_poke_message(payload, cfg.prefilter.poke_text_template)
        synthetic_event = MessageObserved(
            payload=MessagePayload(
                message=message,
                session_id=payload.session_id or address.chat_key,
            ),
            source="poke",
        )
        task = asyncio.create_task(self._handle_observed(synthetic_event, address, cfg))
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self.api.logger.warning(
                "conversation_joiner.task_failed",
                error=f"{type(exc).__name__}: {exc}",
            )

    async def _handle_observed(
        self,
        event: MessageObserved,
        address: ChatAddress,
        cfg: EffectiveJoinerConfig,
    ) -> None:
        chat_key = address.chat_key
        lock = self._locks.setdefault(chat_key, asyncio.Lock())
        async with lock:
            message: InboundMessage = event.payload.message
            session_id = self._resolve_active_session_id(
                address,
                fallback=event.payload.session_id or chat_key,
            )
            now = time.monotonic()

            self._remember_context(chat_key, message, cfg)

            text = message.text.strip()
            keyword_hit = _has_keyword_hint(text, cfg.prefilter.keyword_hints)
            poke_hit = _is_poke_message(message)

            # --- Engagement state machine path ---
            sm = self._sm
            if sm is not None and cfg.engagement.enabled:
                await self._handle_with_state_machine(
                    event,
                    address,
                    cfg,
                    sm,
                    chat_key,
                    session_id,
                    now,
                    message,
                    keyword_hit,
                    poke_hit,
                )
                return

            # --- Original single-trigger path (engagement disabled) ---
            skip_reason = self._prefilter_skip_reason(
                message,
                cfg,
                keyword_hit=keyword_hit,
                poke_hit=poke_hit,
            )
            if skip_reason:
                self.api.logger.debug(
                    "conversation_joiner.prefilter_skipped",
                    reason=skip_reason,
                    chat_key=chat_key,
                    message_id=message.message_id,
                )
                return

            # --- Original single-trigger path (engagement disabled) ---
            if self._is_active_run(session_id):
                self.api.logger.debug(
                    "conversation_joiner.prefilter_skipped",
                    reason="active_run",
                    chat_key=chat_key,
                    session_id=session_id,
                )
                return
            if self._is_debounced(chat_key, cfg, now):
                return
            if self._is_in_cooldown(chat_key, cfg, now):
                return
            if not self._has_hourly_budget(chat_key, cfg, now):
                return
            sample_rate = _select_sample_rate(
                cfg,
                keyword_hit=keyword_hit,
                poke_hit=poke_hit,
            )
            sample_passed, sample_roll = _sample_gate_passes(
                sample_rate,
                self._sample_random,
            )
            if not sample_passed:
                self.api.logger.debug(
                    "conversation_joiner.sample_skipped",
                    chat_key=chat_key,
                    message_id=message.message_id,
                    sample_rate=sample_rate,
                    sample_roll=round(sample_roll, 6),
                    keyword_hit=keyword_hit,
                    poke_hit=poke_hit,
                )
                return

            self._last_decision_at[chat_key] = now
            decision = await self._ask_secretary(message, chat_key, cfg)
            if decision is None:
                return
            if not decision.should_join:
                self.api.logger.debug(
                    "conversation_joiner.decision_skipped",
                    reason="should_join_false",
                    chat_key=chat_key,
                    confidence=decision.confidence,
                )
                return
            if decision.confidence < cfg.threshold or not decision.reason:
                self.api.logger.debug(
                    "conversation_joiner.decision_skipped",
                    reason="below_threshold_or_empty_reason",
                    chat_key=chat_key,
                    confidence=decision.confidence,
                    threshold=cfg.threshold,
                )
                return
            if self._is_active_run(session_id):
                return
            if self._is_in_cooldown(chat_key, cfg, time.monotonic()):
                return
            if not self._has_hourly_budget(chat_key, cfg, time.monotonic()):
                return

            instruction = _build_agent_instruction(decision)
            await self.api.request_agent_response(
                message,
                session_id=session_id,
                reason=decision.reason,
                instruction=instruction,
                attention_frame=self._build_attention_frame(
                    message,
                    chat_key=chat_key,
                    cfg=cfg,
                    decision=decision,
                ),
            )
            triggered_now = time.monotonic()
            self._last_triggered_at[chat_key] = triggered_now
            self._triggered_at.setdefault(chat_key, []).append(triggered_now)
            self.api.logger.info(
                "conversation_joiner.agent_requested",
                chat_key=chat_key,
                session_id=session_id,
                confidence=decision.confidence,
                reason=decision.reason[:200],
            )

    async def _handle_with_state_machine(
        self,
        event: MessageObserved,
        address: ChatAddress,
        cfg: EffectiveJoinerConfig,
        sm: EngagementStateMachine,
        chat_key: str,
        session_id: str,
        now: float,
        message: InboundMessage,
        keyword_hit: bool,
        poke_hit: bool,
    ) -> None:
        """Handle an observed message through the engagement state machine."""
        engagement_cfg = cfg.engagement
        state = sm.get_state(chat_key)
        sm.decay_engagement_score(chat_key, now, engagement_cfg)

        # Check exit conditions BEFORE updating observation timestamp,
        # so idle detection uses the previous observation gap.
        if state.state in ("engaged", "cooling"):
            exit_reason = sm.check_exit_conditions(chat_key, now, engagement_cfg)
            if exit_reason:
                self.api.logger.info(
                    "conversation_joiner.engagement_exit",
                    chat_key=chat_key,
                    reason=exit_reason,
                    prev_state=state.state,
                )
                sm.transition_to_observing(chat_key, now, reason=exit_reason)
                state = sm.get_state(chat_key)

        sm.record_observation(
            chat_key,
            now,
            max_age_seconds=engagement_cfg.exit_gate.activity_window_seconds,
        )

        current_state = state.state

        if current_state == "observing":
            # Prefilter only gates the join gate, not observation bookkeeping.
            skip_reason = self._prefilter_skip_reason(
                message,
                cfg,
                keyword_hit=keyword_hit,
                poke_hit=poke_hit,
            )
            if skip_reason:
                self.api.logger.debug(
                    "conversation_joiner.prefilter_skipped",
                    reason=skip_reason,
                    chat_key=chat_key,
                    message_id=message.message_id,
                )
                return
            await self._handle_observing_state(
                event,
                address,
                cfg,
                sm,
                engagement_cfg,
                chat_key,
                session_id,
                now,
                message,
                keyword_hit,
                poke_hit,
            )

        elif current_state == "joining":
            # Stale joining — if no active run, reset to observing.
            if not self._is_active_run(session_id):
                sm.transition_to_observing(
                    chat_key,
                    now,
                    reason="stale_joining",
                )

        elif current_state == "engaged":
            # Engaged: always append to batch regardless of prefilter.
            batch_full = sm.append_to_batch(chat_key, message, engagement_cfg, now)

            # flush_on_mention: if the bot is mentioned, flush immediately.
            if engagement_cfg.batching.flush_on_mention and message.mentions_bot:
                await self._flush_batch(
                    chat_key, address, cfg, engagement_cfg, session_id
                )
                return

            # Batch full: flush immediately.
            if batch_full:
                self.api.logger.debug(
                    "conversation_joiner.batch_full",
                    chat_key=chat_key,
                )
                await self._flush_batch(
                    chat_key, address, cfg, engagement_cfg, session_id
                )
                return

            # Schedule a window timer if one is not already pending.
            if not sm.has_window_timer(chat_key):
                sm.schedule_window_flush(
                    chat_key,
                    engagement_cfg.batching.window_seconds,
                    lambda ck=chat_key: self._start_window_flush_task(
                        ck, address, cfg, engagement_cfg, session_id
                    ),
                )

        elif current_state == "cooling":
            # Cooling: always append to batch regardless of prefilter.
            sm.append_to_batch(chat_key, message, engagement_cfg, now)
            # Check if the cooling period has elapsed.
            if sm.try_transition_from_cooling(
                chat_key,
                now,
                engagement_cfg.response_cooldown_seconds,
            ):
                self.api.logger.debug(
                    "conversation_joiner.cooling_to_engaged",
                    chat_key=chat_key,
                )
                # Transitioned back to engaged — schedule a flush if batch has messages.
                batch = sm.get_batch(chat_key)
                if batch and batch.messages and not sm.has_window_timer(chat_key):
                    sm.schedule_window_flush(
                        chat_key,
                        engagement_cfg.batching.window_seconds,
                        lambda ck=chat_key: self._start_window_flush_task(
                            ck, address, cfg, engagement_cfg, session_id
                        ),
                    )

    async def _flush_batch(
        self,
        chat_key: str,
        address: ChatAddress,
        cfg: EffectiveJoinerConfig,
        engagement_cfg: EngagementConfig,
        session_id: str,
    ) -> None:
        """Flush the current engagement batch through continue_gate and optionally
        request the agent."""
        sm = self._sm
        if sm is None:
            return
        batch = sm.get_batch(chat_key)
        if batch is None or not batch.messages:
            sm.clear_batch(chat_key)
            return

        # Guard: an accepted request for this chat is already awaiting feedback.
        if chat_key in self._pending_requests:
            self._reschedule_flush(
                chat_key,
                address,
                cfg,
                engagement_cfg,
                session_id,
                cfg.decision_timeout_seconds,
            )
            return

        # Guard: active run.
        if self._is_active_run(session_id):
            self._reschedule_flush(
                chat_key,
                address,
                cfg,
                engagement_cfg,
                session_id,
                cfg.decision_timeout_seconds,
            )
            return

        # Guard: response cooldown — only applies when the bot has already made
        # a proactive engaged-state reply (tracked by state_updated_at in cooling).
        # The initial join has its own cooldown; we don't want to block the first
        # engaged flush for another 45 seconds after the agent already replied.
        state = sm.get_state(chat_key)
        now = time.monotonic()
        sm.decay_engagement_score(chat_key, now, engagement_cfg)
        cooldown = engagement_cfg.response_cooldown_seconds
        if state.state == "cooling" and now - state.state_updated_at < cooldown:
            # Still cooling from a previous proactive reply — reschedule.
            remaining = cooldown - (now - state.state_updated_at)
            self._reschedule_flush(
                chat_key, address, cfg, engagement_cfg, session_id, remaining
            )
            return

        if not self._has_hourly_budget(chat_key, cfg, now):
            self.api.logger.debug(
                "conversation_joiner.batch_budget_exhausted",
                chat_key=chat_key,
                batch_size=len(batch.messages),
            )
            sm.clear_batch(chat_key)
            return

        # Run continue_gate if enabled and enough messages.
        continue_cfg = engagement_cfg.continue_gate
        decision: _SecretaryDecision | None = None
        if continue_cfg.enabled:
            if len(batch.messages) < continue_cfg.min_messages:
                self._reschedule_flush(
                    chat_key,
                    address,
                    cfg,
                    engagement_cfg,
                    session_id,
                    continue_cfg.evaluate_interval_seconds,
                )
                return
            decision = await self._ask_continue_gate(
                batch, chat_key, cfg, engagement_cfg
            )
            if decision is None:
                # Secretary call failed; keep buffering and retry later.
                self._reschedule_flush(
                    chat_key,
                    address,
                    cfg,
                    engagement_cfg,
                    session_id,
                    continue_cfg.evaluate_interval_seconds,
                )
                return
            reply_mode = decision.reply_mode.strip().lower()
            if (
                not decision.should_join
                or decision.confidence < continue_cfg.threshold
                or reply_mode == "no_reply"
            ):
                # continue_gate says no.
                sm.update_engagement_score(
                    chat_key,
                    decision.confidence * 0.5,
                    engagement_cfg.engagement_score_alpha,
                    now,
                )
                if (
                    engagement_cfg.exit_gate.enabled
                    and decision.confidence
                    < engagement_cfg.exit_gate.low_value_threshold
                ):
                    sm.increment_low_value_strike(chat_key)
                self.api.logger.debug(
                    "conversation_joiner.continue_gate_rejected",
                    chat_key=chat_key,
                    confidence=decision.confidence,
                    threshold=continue_cfg.threshold,
                    reply_mode=reply_mode,
                )
                sm.clear_batch(chat_key)
                return
            # continue_gate passed.
            anchor, reply_to_message_id = _select_batch_reply_anchor(
                batch,
                decision,
            )
            instruction = _build_continue_agent_instruction(decision, batch)
        else:
            # continue_gate disabled — flush with last message.
            anchor = batch.messages[-1]
            reply_to_message_id = None
            instruction = _build_engaged_batch_instruction(batch)

        batch_size = len(batch.messages)
        triggered_now = time.monotonic()
        self._last_triggered_at[chat_key] = triggered_now
        self._triggered_at.setdefault(chat_key, []).append(triggered_now)
        state = sm.get_state(chat_key)
        state.last_triggered_at = triggered_now
        state.triggered_timestamps.append(triggered_now)
        self._pending_requests[chat_key] = _PendingRequest(
            requested_at=triggered_now,
            session_id=session_id,
        )
        self._start_monitor(
            chat_key,
            session_id,
            timeout=cfg.decision_timeout_seconds * 3,
            interval=cfg.decision_timeout_seconds * 3,
        )

        try:
            await self.api.request_agent_response(
                anchor,
                session_id=session_id,
                reason=f"engaged continue (batch of {batch_size})",
                instruction=instruction,
                observed_messages=tuple(batch.messages),
                reply_to_message_id=reply_to_message_id,
                attention_frame=AttentionFrame(
                    trigger_kind="engaged_continue",
                    anchor_message_id=anchor.message_id,
                    messages=tuple(batch.messages),
                    reason=decision.reason if decision is not None else "",
                    focus=decision.focus if decision is not None else "",
                    reply_to_message_id=reply_to_message_id,
                    max_chars=engagement_cfg.batching.max_chars,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self._clear_pending_request(chat_key)
            self._reschedule_flush(
                chat_key,
                address,
                cfg,
                engagement_cfg,
                session_id,
                cfg.decision_timeout_seconds,
            )
            self.api.logger.warning(
                "conversation_joiner.engaged_agent_request_failed",
                chat_key=chat_key,
                session_id=session_id,
                error=f"{type(exc).__name__}: {exc}",
            )
            return

        sm.clear_batch(chat_key)

        self.api.logger.info(
            "conversation_joiner.engaged_agent_requested",
            chat_key=chat_key,
            session_id=session_id,
            batch_size=batch_size,
        )

    def _start_window_flush_task(
        self,
        chat_key: str,
        address: ChatAddress,
        cfg: EffectiveJoinerConfig,
        engagement_cfg: EngagementConfig,
        session_id: str,
    ) -> None:
        task = asyncio.create_task(
            self._window_flush_callback(
                chat_key,
                address,
                cfg,
                engagement_cfg,
                session_id,
            )
        )
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)

    async def _window_flush_callback(
        self,
        chat_key: str,
        address: ChatAddress,
        cfg: EffectiveJoinerConfig,
        engagement_cfg: EngagementConfig,
        session_id: str,
    ) -> None:
        """Called by the asyncio timer when the batch window expires."""
        sm = self._sm
        if sm is not None:
            sm.mark_window_timer_fired(chat_key)
        # Run flush under the per-chat-key lock to avoid races.
        lock = self._locks.setdefault(chat_key, asyncio.Lock())
        async with lock:
            await self._flush_batch(chat_key, address, cfg, engagement_cfg, session_id)

    def _reschedule_flush(
        self,
        chat_key: str,
        address: ChatAddress,
        cfg: EffectiveJoinerConfig,
        engagement_cfg: EngagementConfig,
        session_id: str,
        delay_seconds: float,
    ) -> None:
        """Reschedule a window flush after a short delay (used when blocked by
        cooldown or active run)."""
        sm = self._sm
        if sm is None:
            return
        state = sm.get_state(chat_key)
        # Only reschedule if still in a state that would flush.
        if state.state not in ("engaged", "cooling"):
            return
        sm.schedule_window_flush(
            chat_key,
            delay_seconds,
            lambda ck=chat_key: self._start_window_flush_task(
                ck, address, cfg, engagement_cfg, session_id
            ),
        )
        self.api.logger.debug(
            "conversation_joiner.flush_rescheduled",
            chat_key=chat_key,
            delay_seconds=round(delay_seconds, 1),
        )

    async def _ask_continue_gate(
        self,
        batch: Any,  # ObservedMessageBatch
        chat_key: str,
        cfg: EffectiveJoinerConfig,
        engagement_cfg: EngagementConfig,
    ) -> _SecretaryDecision | None:
        """Run the cheaper continue gate on a batch of buffered messages."""
        persona_context = await self._load_persona_context(cfg)
        prompt = self._build_continue_gate_prompt(
            batch, chat_key, cfg, persona_context=persona_context
        )
        try:
            response = await asyncio.wait_for(
                self.api.llm_chat(
                    [
                        {"role": "system", "content": _CONTINUE_GATE_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    model=engagement_cfg.continue_gate.model,
                    temperature=0.0,
                    max_tokens=200,
                    tools=[],
                ),
                timeout=cfg.decision_timeout_seconds,
            )
        except TimeoutError:
            self.api.logger.debug(
                "conversation_joiner.continue_gate_timeout",
                chat_key=chat_key,
            )
            return None
        except Exception as exc:  # noqa: BLE001
            self.api.logger.warning(
                "conversation_joiner.continue_gate_failed",
                chat_key=chat_key,
                error=f"{type(exc).__name__}: {exc}",
            )
            return None
        return _parse_decision(str(getattr(response, "content", "") or ""))

    def _build_continue_gate_prompt(
        self,
        batch: Any,
        chat_key: str,
        cfg: EffectiveJoinerConfig,
        *,
        persona_context: str = "",
    ) -> str:
        context = _format_context(self._contexts.get(chat_key, ()), cfg)
        lines: list[str] = []
        for msg in batch.messages:
            sender = msg.user_id
            if msg.sender_context is not None:
                sender = (
                    msg.sender_context.display_name
                    or msg.sender_context.platform_user_id
                    or msg.user_id
                )
            text = msg.text.strip() if msg.text else ""
            msg_id = msg.message_id or "(no-message-id)"
            tags = _format_extra_tags(msg)
            lines.append(f"- [{msg_id}] {sender}:{tags} {text}")
        batch_text = "\n".join(lines)
        return (
            f"Chat: {chat_key}\n"
            "Bot persona context for judging whether continuing fits:\n"
            f"{persona_context or '(none)'}\n\n"
            f"Recent context:\n{context or '(none)'}\n\n"
            f"Batch of {len(batch.messages)} new messages:\n{batch_text}\n\n"
            "Decide whether the bot should respond now."
        )

    async def _handle_observing_state(
        self,
        event: MessageObserved,
        address: ChatAddress,
        cfg: EffectiveJoinerConfig,
        sm: EngagementStateMachine,
        engagement_cfg: EngagementConfig,
        chat_key: str,
        session_id: str,
        now: float,
        message: InboundMessage,
        keyword_hit: bool,
        poke_hit: bool,
    ) -> None:
        """Run the join gate pipeline when in the observing state."""
        if self._is_active_run(session_id):
            return
        if self._is_debounced(chat_key, cfg, now):
            return
        if self._is_in_cooldown(chat_key, cfg, now):
            return
        if not self._has_hourly_budget(chat_key, cfg, now):
            return
        sample_rate = _select_sample_rate(
            cfg,
            keyword_hit=keyword_hit,
            poke_hit=poke_hit,
        )
        sample_passed, sample_roll = _sample_gate_passes(
            sample_rate,
            self._sample_random,
        )
        if not sample_passed:
            self.api.logger.debug(
                "conversation_joiner.sample_skipped",
                chat_key=chat_key,
                message_id=message.message_id,
                sample_rate=sample_rate,
                sample_roll=round(sample_roll, 6),
                keyword_hit=keyword_hit,
                poke_hit=poke_hit,
            )
            return

        self._last_decision_at[chat_key] = now
        decision = await self._ask_secretary(message, chat_key, cfg)
        if decision is None:
            return
        if not decision.should_join:
            self.api.logger.debug(
                "conversation_joiner.decision_skipped",
                reason="should_join_false",
                chat_key=chat_key,
                confidence=decision.confidence,
            )
            return
        if decision.confidence < cfg.threshold or not decision.reason:
            self.api.logger.debug(
                "conversation_joiner.decision_skipped",
                reason="below_threshold_or_empty_reason",
                chat_key=chat_key,
                confidence=decision.confidence,
                threshold=cfg.threshold,
            )
            return

        # Double-check guards after the async secretary call.
        now = time.monotonic()
        if self._is_active_run(session_id):
            return
        if self._is_in_cooldown(chat_key, cfg, now):
            return
        if not self._has_hourly_budget(chat_key, cfg, now):
            return

        # Transition to joining and request the agent.
        sm.transition_to_joining(chat_key, now)
        instruction = _build_agent_instruction(decision)
        triggered_now = time.monotonic()
        self._last_triggered_at[chat_key] = triggered_now
        self._triggered_at.setdefault(chat_key, []).append(triggered_now)
        state = sm.get_state(chat_key)
        state.last_triggered_at = triggered_now
        state.triggered_timestamps.append(triggered_now)
        self._pending_requests[chat_key] = _PendingRequest(
            requested_at=triggered_now,
            session_id=session_id,
        )
        self._start_monitor(
            chat_key,
            session_id,
            timeout=cfg.decision_timeout_seconds * 3,
            interval=cfg.decision_timeout_seconds * 3,
        )

        try:
            await self.api.request_agent_response(
                message,
                session_id=session_id,
                reason=decision.reason,
                instruction=instruction,
                attention_frame=self._build_attention_frame(
                    message,
                    chat_key=chat_key,
                    cfg=cfg,
                    decision=decision,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self._clear_pending_request(chat_key)
            sm.transition_to_observing(
                chat_key, time.monotonic(), reason="request_failed"
            )
            self.api.logger.warning(
                "conversation_joiner.agent_request_failed",
                chat_key=chat_key,
                session_id=session_id,
                error=f"{type(exc).__name__}: {exc}",
            )
            return

        self.api.logger.info(
            "conversation_joiner.agent_requested",
            chat_key=chat_key,
            session_id=session_id,
            confidence=decision.confidence,
            reason=decision.reason[:200],
            engagement_state="joining",
        )

    async def _on_message_sent(self, event: MessageSent) -> None:
        """Detect agent replies and update engagement state accordingly."""
        sm = self._sm
        if sm is None:
            return

        payload = event.payload
        chat_key = self._pending_chat_key_for_sent(
            payload.message,
            payload.session_id,
        )
        if chat_key is None:
            self._engage_after_direct_response(payload.message)
            return

        now = time.monotonic()
        state = sm.get_state(chat_key)

        if state.state == "joining":
            sm.transition_to_engaged(chat_key, now)
            self.api.logger.info(
                "conversation_joiner.engagement_confirmed",
                chat_key=chat_key,
                session_id=payload.session_id,
                state="engaged",
            )
        elif state.state in ("engaged", "cooling"):
            engagement_cfg = effective_group_config(self._config, chat_key).engagement
            sm.decay_engagement_score(
                chat_key,
                now,
                engagement_cfg,
            )
            sm.reset_low_value_strikes(chat_key)
            sm.update_engagement_score(
                chat_key,
                0.8,
                engagement_cfg.engagement_score_alpha,
                now,
            )
            state.last_agent_reply_at = now
            if state.state == "engaged":
                sm.transition_to_cooling(chat_key, now)

        self._clear_pending_request(chat_key)

    def _engage_after_direct_response(self, message: InboundMessage) -> None:
        """Treat a visible reactive group reply as participation.

        Mention-triggered messages are emitted as ``MessageReceived`` rather
        than ``MessageObserved``, so the joiner never sees them on its normal
        observation path. Once the router has sent a visible reply, refresh the
        group engagement state and remember the triggering message. This keeps
        the bot attentive to natural follow-ups instead of immediately falling
        back to random observing.
        """
        sm = self._sm
        sender = message.sender_context
        if (
            sm is None
            or not message.is_group
            or _is_command(message)
            or (sender is not None and (sender.is_self or sender.is_bot))
        ):
            return
        address = _address_from_message(message)
        if address is None:
            return
        chat_key = address.chat_key
        cfg = effective_group_config(self._config, chat_key)
        if not cfg.enabled or not cfg.engagement.enabled:
            return

        message_id = message.message_id
        if (
            message_id
            and self._last_direct_engaged_message_id.get(chat_key) == message_id
        ):
            # Streaming output may publish MessageSent more than once for the
            # same inbound trigger. Do not reset the batch repeatedly.
            sm.get_state(chat_key).last_agent_reply_at = time.monotonic()
            return
        if message_id:
            self._last_direct_engaged_message_id[chat_key] = message_id

        now = time.monotonic()
        self._remember_context(chat_key, message, cfg)
        sm.record_observation(
            chat_key,
            now,
            max_age_seconds=cfg.engagement.exit_gate.activity_window_seconds,
        )
        sm.transition_to_engaged(chat_key, now)
        sm.reset_low_value_strikes(chat_key)
        sm.update_engagement_score(
            chat_key,
            0.8,
            cfg.engagement.engagement_score_alpha,
            now,
        )
        self.api.logger.info(
            "conversation_joiner.direct_trigger_engaged",
            chat_key=chat_key,
            message_id=message_id,
        )

    def _start_monitor(
        self,
        chat_key: str,
        session_id: str,
        *,
        timeout: float,
        interval: float,
    ) -> None:
        """Start or replace the NO_REPLY monitor for one chat."""
        self._cancel_monitor(chat_key)
        monitor = asyncio.create_task(
            self._monitor_agent_run(
                chat_key,
                session_id,
                timeout=timeout,
                interval=interval,
            ),
        )
        self._monitor_tasks[chat_key] = monitor
        self._tasks.add(monitor)
        monitor.add_done_callback(
            lambda task, ck=chat_key: self._on_monitor_done(ck, task)
        )

    def _on_monitor_done(self, chat_key: str, task: asyncio.Task[None]) -> None:
        if self._monitor_tasks.get(chat_key) is task:
            self._monitor_tasks.pop(chat_key, None)
        self._on_task_done(task)

    def _cancel_monitor(self, chat_key: str) -> None:
        monitor = self._monitor_tasks.pop(chat_key, None)
        if monitor is not None and not monitor.done():
            monitor.cancel()

    def _clear_pending_request(
        self,
        chat_key: str,
        *,
        cancel_monitor: bool = True,
    ) -> None:
        self._pending_requests.pop(chat_key, None)
        if cancel_monitor:
            self._cancel_monitor(chat_key)

    def _pending_chat_key_for_sent(
        self,
        message: InboundMessage,
        session_id: str,
    ) -> str | None:
        address = _address_from_message(message)
        if address is not None:
            chat_key = address.chat_key
            pending = self._pending_requests.get(chat_key)
            if pending is not None and (
                not session_id or pending.session_id == session_id
            ):
                return chat_key
            if pending is not None and session_id:
                active_session_id = self._resolve_active_session_id(
                    address,
                    fallback=chat_key,
                )
                if active_session_id == session_id:
                    return chat_key

        if session_id:
            for chat_key, pending in self._pending_requests.items():
                if pending.session_id == session_id:
                    return chat_key

        return None

    async def _monitor_agent_run(
        self,
        chat_key: str,
        session_id: str,
        timeout: float,
        interval: float,
    ) -> None:
        """Wait for an agent run to complete, then detect NO_REPLY.

        The monitor keeps polling while the run is active.  It is cancelled
        immediately when a matching MessageSent confirms a visible reply.
        """
        await asyncio.sleep(timeout)
        sm = self._sm
        if sm is None:
            return

        while chat_key in self._pending_requests and self._is_active_run(session_id):
            await asyncio.sleep(interval)

        # Run finished without MessageSent → treat as NO_REPLY.
        if chat_key not in self._pending_requests:
            return

        now = time.monotonic()
        state = sm.get_state(chat_key)

        if state.state == "joining":
            engagement_cfg = effective_group_config(self._config, chat_key).engagement
            sm.transition_to_observing(chat_key, now, reason="agent_no_reply")
            sm.update_engagement_score(
                chat_key,
                0.1,
                engagement_cfg.engagement_score_alpha,
                now,
            )
            self.api.logger.info(
                "conversation_joiner.no_reply_detected",
                chat_key=chat_key,
                session_id=session_id,
                from_state="joining",
            )
        elif state.state in ("engaged", "cooling"):
            engagement_cfg = effective_group_config(self._config, chat_key).engagement
            sm.decay_engagement_score(
                chat_key,
                now,
                engagement_cfg,
            )
            sm.increment_low_value_strike(chat_key)
            sm.update_engagement_score(
                chat_key,
                0.1,
                engagement_cfg.engagement_score_alpha,
                now,
            )
            self.api.logger.debug(
                "conversation_joiner.no_reply_detected",
                chat_key=chat_key,
                session_id=session_id,
                from_state=state.state,
            )

        self._clear_pending_request(chat_key, cancel_monitor=False)

    def _remember_context(
        self,
        chat_key: str,
        message: InboundMessage,
        cfg: EffectiveJoinerConfig,
    ) -> None:
        text = message.text.strip()
        if not text:
            return
        sender = message.user_id
        if message.sender_context is not None:
            sender = (
                message.sender_context.display_name
                or message.sender_context.platform_user_id
                or message.user_id
            )
        entries = self._contexts.setdefault(chat_key, deque())
        if (
            entries
            and message.message_id
            and entries[-1].message_id == message.message_id
        ):
            return
        entries.append(
            _ContextEntry(
                sender=sender,
                text=text,
                timestamp=message.timestamp,
                message_id=message.message_id,
                message=message,
            )
        )
        while len(entries) > cfg.max_context_messages:
            entries.popleft()

    def _build_attention_frame(
        self,
        anchor: InboundMessage,
        *,
        chat_key: str,
        cfg: EffectiveJoinerConfig,
        decision: _SecretaryDecision,
    ) -> AttentionFrame:
        selected = _select_context_entries(self._contexts.get(chat_key, ()), cfg)
        return AttentionFrame(
            trigger_kind="proactive_join",
            anchor_message_id=anchor.message_id,
            messages=tuple(entry.message for entry in selected),
            reason=decision.reason,
            focus=decision.focus,
            reply_to_message_id=(decision.reply_anchor_message_id or None),
            max_chars=cfg.max_context_chars,
        )

    def _prefilter_skip_reason(
        self,
        message: InboundMessage,
        cfg: EffectiveJoinerConfig,
        *,
        keyword_hit: bool,
        poke_hit: bool = False,
    ) -> str:
        sender = message.sender_context
        if sender is not None and (sender.is_self or sender.is_bot):
            return "bot_or_self"
        text = message.text.strip()
        if not text:
            return "empty_text"
        if cfg.prefilter.ignore_mentions and message.mentions_bot:
            return "mention"
        if cfg.prefilter.ignore_commands and _is_command(message):
            return "command"
        # Keyword and poke messages bypass the min-length floor: their signal is
        # carried by the classification, not by text length.
        if (
            len(text) < cfg.prefilter.min_text_chars
            and not keyword_hit
            and not poke_hit
        ):
            return "too_short"
        return ""

    async def _ask_secretary(
        self,
        message: InboundMessage,
        chat_key: str,
        cfg: EffectiveJoinerConfig,
    ) -> _SecretaryDecision | None:
        persona_context = await self._load_persona_context(cfg)
        prompt = self._build_secretary_prompt(
            message,
            chat_key,
            cfg,
            persona_context=persona_context,
        )
        try:
            response = await asyncio.wait_for(
                self.api.llm_chat(
                    [
                        {
                            "role": "system",
                            "content": _SECRETARY_SYSTEM_PROMPT,
                        },
                        {"role": "user", "content": prompt},
                    ],
                    model=cfg.model,
                    temperature=0.0,
                    max_tokens=300,
                    tools=[],
                ),
                timeout=cfg.decision_timeout_seconds,
            )
        except TimeoutError:
            self.api.logger.debug(
                "conversation_joiner.secretary_timeout",
                chat_key=chat_key,
                timeout=cfg.decision_timeout_seconds,
            )
            return None
        except Exception as exc:  # noqa: BLE001
            self.api.logger.warning(
                "conversation_joiner.secretary_failed",
                chat_key=chat_key,
                error=f"{type(exc).__name__}: {exc}",
            )
            return None

        decision = _parse_decision(str(getattr(response, "content", "") or ""))
        if decision is None:
            self.api.logger.debug(
                "conversation_joiner.secretary_parse_failed",
                chat_key=chat_key,
            )
        return decision

    def _build_secretary_prompt(
        self,
        message: InboundMessage,
        chat_key: str,
        cfg: EffectiveJoinerConfig,
        *,
        persona_context: str = "",
    ) -> str:
        context = _format_context(self._contexts.get(chat_key, ()), cfg)
        current_sender = message.user_id
        if message.sender_context is not None:
            current_sender = (
                message.sender_context.display_name
                or message.sender_context.platform_user_id
                or message.user_id
            )
        return (
            f"Chat: {chat_key}\n"
            f"Threshold: {cfg.threshold}\n"
            f"Cooldown seconds: {cfg.cooldown_seconds}\n"
            "Bot persona context for judging whether joining fits the main "
            f"agent, not for drafting the reply:\n{persona_context or '(none)'}\n\n"
            f"Recent context:\n{context or '(none)'}\n\n"
            f"Current message from {current_sender}:{_format_extra_tags(message)}\n"
            f"{message.text.strip()}\n\n"
            "Decide whether the main bot should naturally join now."
        )

    async def _load_persona_context(self, cfg: EffectiveJoinerConfig) -> str:
        persona_cfg = cfg.persona_context
        if (
            not persona_cfg.enabled
            or persona_cfg.max_chars <= 0
            or not persona_cfg.files
        ):
            return ""

        now = time.monotonic()
        cached = self._persona_context_cache
        if (
            cached is not None
            and persona_cfg.cache_ttl_seconds > 0
            and now - cached.loaded_at < persona_cfg.cache_ttl_seconds
        ):
            return cached.text

        async with self._persona_context_lock:
            cached = self._persona_context_cache
            now = time.monotonic()
            if (
                cached is not None
                and persona_cfg.cache_ttl_seconds > 0
                and now - cached.loaded_at < persona_cfg.cache_ttl_seconds
            ):
                return cached.text

            text = await self._read_persona_context_files(cfg)
            self._persona_context_cache = _PersonaContextCache(
                text=text,
                loaded_at=time.monotonic(),
            )
            self.api.logger.debug(
                "conversation_joiner.persona_context_loaded",
                file_count=len(persona_cfg.files),
                char_count=len(text),
            )
            return text

    async def _read_persona_context_files(self, cfg: EffectiveJoinerConfig) -> str:
        persona_cfg = cfg.persona_context
        remaining = persona_cfg.max_chars
        parts: list[str] = []
        for path in persona_cfg.files:
            clean_path = path.strip()
            if not clean_path or remaining <= 0:
                continue
            try:
                raw = await self.api.workspace_read(clean_path)
            except Exception as exc:  # noqa: BLE001
                self.api.logger.debug(
                    "conversation_joiner.persona_context_file_skipped",
                    path=clean_path,
                    error=f"{type(exc).__name__}: {exc}",
                )
                continue

            content = str(raw or "").strip()
            if not content:
                continue
            block = f"### {clean_path}\n{content}\n"
            if len(block) > remaining:
                block = block[:remaining]
            parts.append(block)
            remaining -= len(block)
        return "\n".join(parts).strip()

    def _is_debounced(
        self,
        chat_key: str,
        cfg: EffectiveJoinerConfig,
        now: float,
    ) -> bool:
        last = self._last_decision_at.get(chat_key, 0.0)
        return cfg.debounce_seconds > 0 and now - last < cfg.debounce_seconds

    def _is_in_cooldown(
        self,
        chat_key: str,
        cfg: EffectiveJoinerConfig,
        now: float,
    ) -> bool:
        last = self._last_triggered_at.get(chat_key, 0.0)
        return cfg.cooldown_seconds > 0 and now - last < cfg.cooldown_seconds

    def _has_hourly_budget(
        self,
        chat_key: str,
        cfg: EffectiveJoinerConfig,
        now: float,
    ) -> bool:
        if cfg.max_triggers_per_hour <= 0:
            return False
        recent = [ts for ts in self._triggered_at.get(chat_key, []) if now - ts < 3600]
        self._triggered_at[chat_key] = recent
        return len(recent) < cfg.max_triggers_per_hour

    def _resolve_active_session_id(
        self,
        address: ChatAddress,
        *,
        fallback: str,
    ) -> str:
        resolver = getattr(self.api, "get_active_session_id", None)
        if not callable(resolver):
            return fallback
        try:
            resolved = resolver(address)
        except Exception:  # noqa: BLE001
            return fallback
        return str(resolved or fallback)

    def _is_active_run(self, session_id: str) -> bool:
        try:
            status = self.api.get_session_run_status(session_id)
        except Exception:  # noqa: BLE001
            return False
        return bool(status.get("active"))

    async def _status_provider(self, *, session_id: str, chat_key: str) -> str | None:
        """Return engagement state info for /status."""
        sm = self._sm
        if sm is None:
            return None
        state = sm._states.get(chat_key)
        if state is None:
            return None
        cfg = effective_group_config(self._config, chat_key)
        if not cfg.engagement.enabled:
            return None
        sm.decay_engagement_score(chat_key, time.monotonic(), cfg.engagement)
        lines = [f"Engagement: {state.state}"]
        if state.state not in ("observing",):
            lines.append(f"  Score: {state.engagement_score:.2f}")
            lines.append(f"  Low-value strikes: {state.low_value_strikes}")
            batch = sm.get_batch(chat_key)
            batch_count = len(batch.messages) if batch else 0
            lines.append(f"  Batch messages: {batch_count}")
            lines.append(f"  Pending request: {chat_key in self._pending_requests}")
        return "\n".join(lines)


_SECRETARY_SYSTEM_PROMPT = (
    "You are a cheap conversation gate for a group-chat bot. Decide only whether "
    "the main agent should naturally join the current topic. You do not write "
    "the final message. Return only valid JSON with keys: should_join boolean, "
    "confidence number from 0 to 1, reason string, entry_style string, focus "
    "string. Prefer false unless joining is timely and useful."
)

_CONTINUE_GATE_SYSTEM_PROMPT = (
    "You are a cheap conversation continuation gate for a group-chat bot that is "
    "already engaged in a topic. You receive a batch of recent messages and decide "
    "whether the bot should respond NOW. The bot has already joined; you only "
    "decide timing. Return only valid JSON with keys: should_join boolean, "
    "confidence number from 0 to 1, reason string, entry_style string, focus "
    "string, reply_mode string, reply_anchor_message_id string. reply_mode must "
    "be direct_reply, group_comment, or no_reply. Use direct_reply when the bot "
    "should answer one specific batch message and set reply_anchor_message_id to "
    "that message id. Use group_comment for an ambient contribution to the whole "
    "batch and leave reply_anchor_message_id empty. Prefer false unless the batch "
    "contains something the bot should directly address."
)


def _address_from_message(message: InboundMessage) -> ChatAddress | None:
    if not message.is_group:
        return None
    chat_type = ""
    if message.chat_context and message.chat_context.chat_type:
        chat_type = message.chat_context.chat_type
    elif message.message_context and message.message_context.chat_type:
        chat_type = message.message_context.chat_type
    address = ChatAddress.from_inbound(
        message.platform,
        message.chat_id,
        is_group=message.is_group,
        chat_type=chat_type,
    )
    if not address.is_typed or address.target_type != "group":
        return None
    return address


def _synthesize_poke_message(
    payload: PokePayload,
    text_template: str,
) -> InboundMessage:
    """Build a tagged InboundMessage from a group PokeEvent.

    The poke becomes an ordinary ambient message carrying a ``poke`` marker (in
    ``extra_tags`` and ``raw_event``) so it flows through the existing gate and
    batching unchanged. ``mentions_bot`` is deliberately ``False`` so the
    engaged-state ``flush_on_mention`` path does not treat it as a strong signal.
    """
    channel = payload.chat_address.channel or "milky"
    group_id = payload.group_id or payload.chat_address.target_id
    poker = payload.user_id
    now = time.monotonic()
    text = text_template.format(poker=poker)
    raw: dict[str, Any] = {"poke": True, "scene": payload.scene}
    raw.update(payload.raw)
    return InboundMessage(
        message_id=f"poke:{poker}:{now}",
        platform=channel,
        chat_id=group_id,
        user_id=poker,
        text=text,
        raw_event=raw,
        is_group=True,
        timestamp=now,
        chat_context=ChatContext(
            platform=channel,
            chat_type="group",
            platform_chat_id=group_id,
        ),
        sender_context=SenderContext(
            display_name="",
            platform_user_id=poker,
            is_self=False,
            is_bot=False,
        ),
        message_context=MessageContext(
            timestamp=now,
            channel=channel,
            chat_type="group",
            chat_id=group_id,
            sender_id=poker,
            extra_tags=("poke",),
        ),
        mentions_bot=False,
    )


def _is_command(message: InboundMessage) -> bool:
    prefix = message.command_prefix or "/"
    return bool(prefix and message.text.lstrip().startswith(prefix))


def _has_keyword_hint(text: str, hints: list[str]) -> bool:
    lowered = text.lower()
    return any(hint.lower() in lowered for hint in hints if hint)


def _is_poke_message(message: InboundMessage) -> bool:
    """Return True for synthesized poke messages (tagged via ``extra_tags``)."""
    ctx = message.message_context
    return ctx is not None and "poke" in ctx.extra_tags


def _format_extra_tags(message: InboundMessage) -> str:
    """Render a message's ``extra_tags`` as a trailing marker, e.g. ``<poke>``.

    Empty for ordinary messages so prompts are unchanged when no tags are set.
    """
    ctx = message.message_context
    if ctx is None or not ctx.extra_tags:
        return ""
    return " " + " ".join(f"<{tag}>" for tag in ctx.extra_tags)


def _select_sample_rate(
    cfg: EffectiveJoinerConfig,
    *,
    keyword_hit: bool,
    poke_hit: bool,
) -> float:
    """Pick the pre-secretary sample rate by input class.

    Three tiers: poke (weak signal, lowest) < ambient < keyword. Poke is only
    eligible when ``enable_poke`` is on; otherwise it falls back to ambient so a
    stray tagged message is never silently gated to zero.
    """
    if poke_hit and cfg.prefilter.enable_poke:
        return cfg.prefilter.poke_sample_rate
    if keyword_hit:
        return cfg.prefilter.keyword_sample_rate
    return cfg.prefilter.sample_rate


def _sample_gate_passes(
    sample_rate: float,
    random_fn: Callable[[], float],
) -> tuple[bool, float]:
    if sample_rate >= 1.0:
        return True, 0.0
    if sample_rate <= 0.0:
        return False, 1.0
    roll = random_fn()
    return roll < sample_rate, roll


def _format_context(
    entries: Iterable[_ContextEntry],
    cfg: EffectiveJoinerConfig,
) -> str:
    selected = _select_context_entries(entries, cfg)
    lines: list[str] = []
    remaining = cfg.max_context_chars
    for entry in selected:
        line = f"- {entry.sender}: {entry.text}"
        if len(line) > remaining:
            lines.append(line[:remaining])
            break
        lines.append(line)
        remaining -= len(line)
        if remaining <= 0:
            break
    return "\n".join(lines)


def _select_context_entries(
    entries: Iterable[_ContextEntry],
    cfg: EffectiveJoinerConfig,
) -> list[_ContextEntry]:
    candidates = list(entries)[-cfg.max_context_messages :]
    selected_reversed: list[_ContextEntry] = []
    remaining = cfg.max_context_chars
    for entry in reversed(candidates):
        line_size = len(f"- {entry.sender}: {entry.text}")
        if line_size > remaining and selected_reversed:
            break
        selected_reversed.append(entry)
        remaining -= min(line_size, remaining)
        if remaining <= 0:
            break
    selected_reversed.reverse()
    return selected_reversed


def _parse_decision(content: str) -> _SecretaryDecision | None:
    payload = _extract_json_object(content)
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return _SecretaryDecision(
        should_join=_coerce_bool(data.get("should_join")),
        confidence=_coerce_float(data.get("confidence")),
        reason=str(data.get("reason") or "").strip(),
        entry_style=str(data.get("entry_style") or "").strip(),
        focus=str(data.get("focus") or "").strip(),
        reply_mode=str(data.get("reply_mode") or "").strip(),
        reply_anchor_message_id=str(data.get("reply_anchor_message_id") or "").strip(),
    )


def _extract_json_object(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    if text.startswith("{") and text.endswith("}"):
        return text
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    return match.group(0) if match else ""


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    return False


def _coerce_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _build_agent_instruction(decision: _SecretaryDecision) -> str:
    parts = [
        "You are joining the group conversation proactively. Do not imply that "
        "a user directly summoned you.",
        f"Secretary reason: {decision.reason}",
    ]
    if decision.entry_style:
        parts.append(f"Suggested entry style: {decision.entry_style}")
    if decision.focus:
        parts.append(f"Focus: {decision.focus}")
    parts.append("Keep it short. If the moment has passed, reply NO_REPLY.")
    return "\n".join(parts)


def _build_continue_agent_instruction(
    decision: _SecretaryDecision,
    batch: Any,
) -> str:
    parts = [
        "You are continuing in an ongoing group conversation. The bot was already "
        "engaged and the system collected recent messages that may warrant a response.",
        f"Continue gate reason: {decision.reason}",
    ]
    if decision.focus:
        parts.append(f"Focus: {decision.focus}")
    parts.append(
        f"The batch contains {len(batch.messages)} recent messages. "
        "Respond to what's relevant. If the moment has passed, reply NO_REPLY."
    )
    return "\n".join(parts)


def _select_batch_reply_anchor(
    batch: Any,
    decision: _SecretaryDecision,
) -> tuple[InboundMessage, str | None]:
    messages = list(batch.messages)
    fallback = messages[-1]
    by_id = {msg.message_id: msg for msg in messages if msg.message_id}
    mode = decision.reply_mode.strip().lower()
    anchor_id = decision.reply_anchor_message_id.strip()

    if mode == "direct_reply":
        if anchor_id and anchor_id in by_id:
            return by_id[anchor_id], anchor_id
        if not anchor_id and len(messages) == 1 and fallback.message_id:
            return fallback, fallback.message_id
        return fallback, ""

    if mode == "group_comment" or mode == "no_reply":
        return fallback, ""

    if anchor_id and anchor_id in by_id:
        return by_id[anchor_id], anchor_id

    return fallback, ""


def _build_engaged_batch_instruction(batch: Any) -> str:
    return (
        "You are continuing in an ongoing group conversation. The system collected "
        f"{len(batch.messages)} recent messages that may warrant a response. "
        "Respond to what's relevant. If the moment has passed, reply NO_REPLY."
    )
