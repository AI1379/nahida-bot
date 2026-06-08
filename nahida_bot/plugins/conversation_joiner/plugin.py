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
from nahida_bot.core.events import MessageObserved
from nahida_bot.plugins.base import InboundMessage, Plugin
from nahida_bot.plugins.conversation_joiner.config import (
    EffectiveJoinerConfig,
    effective_group_config,
    parse_conversation_joiner_config,
)


@dataclass(slots=True, frozen=True)
class _ContextEntry:
    sender: str
    text: str
    timestamp: float
    message_id: str


@dataclass(slots=True, frozen=True)
class _SecretaryDecision:
    should_join: bool
    confidence: float
    reason: str
    entry_style: str = ""
    focus: str = ""


@dataclass(slots=True, frozen=True)
class _PersonaContextCache:
    text: str
    loaded_at: float


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

    async def on_load(self) -> None:
        self.api.subscribe(MessageObserved, self._on_message_observed)
        self.api.logger.info(
            "conversation_joiner.loaded",
            enabled=self._config.enabled,
            group_count=len(self._config.groups),
        )

    async def on_unload(self) -> None:
        await self._cancel_tasks()

    async def on_disable(self) -> None:
        await self._cancel_tasks()

    async def _cancel_tasks(self) -> None:
        if not self._tasks:
            return
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

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
            session_id = event.payload.session_id or chat_key
            now = time.monotonic()

            self._remember_context(chat_key, message, cfg)

            text = message.text.strip()
            keyword_hit = _has_keyword_hint(text, cfg.prefilter.keyword_hints)
            skip_reason = self._prefilter_skip_reason(
                message,
                cfg,
                keyword_hit=keyword_hit,
            )
            if skip_reason:
                self.api.logger.debug(
                    "conversation_joiner.prefilter_skipped",
                    reason=skip_reason,
                    chat_key=chat_key,
                    message_id=message.message_id,
                )
                return
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
            sample_rate = (
                cfg.prefilter.keyword_sample_rate
                if keyword_hit
                else cfg.prefilter.sample_rate
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
        entries.append(
            _ContextEntry(
                sender=sender,
                text=text,
                timestamp=message.timestamp,
                message_id=message.message_id,
            )
        )
        while len(entries) > cfg.max_context_messages:
            entries.popleft()

    def _prefilter_skip_reason(
        self,
        message: InboundMessage,
        cfg: EffectiveJoinerConfig,
        *,
        keyword_hit: bool,
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
        if len(text) < cfg.prefilter.min_text_chars and not keyword_hit:
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
            f"Current message from {current_sender}:\n{message.text.strip()}\n\n"
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

    def _is_active_run(self, session_id: str) -> bool:
        try:
            status = self.api.get_session_run_status(session_id)
        except Exception:  # noqa: BLE001
            return False
        return bool(status.get("active"))


_SECRETARY_SYSTEM_PROMPT = (
    "You are a cheap conversation gate for a group-chat bot. Decide only whether "
    "the main agent should naturally join the current topic. You do not write "
    "the final message. Return only valid JSON with keys: should_join boolean, "
    "confidence number from 0 to 1, reason string, entry_style string, focus "
    "string. Prefer false unless joining is timely and useful."
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


def _is_command(message: InboundMessage) -> bool:
    prefix = message.command_prefix or "/"
    return bool(prefix and message.text.lstrip().startswith(prefix))


def _has_keyword_hint(text: str, hints: list[str]) -> bool:
    lowered = text.lower()
    return any(hint.lower() in lowered for hint in hints if hint)


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
    selected = list(entries)[-cfg.max_context_messages :]
    lines: list[str] = []
    remaining = cfg.max_context_chars
    for entry in reversed(selected):
        line = f"- {entry.sender}: {entry.text}"
        if len(line) > remaining:
            if not lines:
                lines.append(line[:remaining])
            break
        lines.append(line)
        remaining -= len(line)
        if remaining <= 0:
            break
    lines.reverse()
    return "\n".join(lines)


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
