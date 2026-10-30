"""Persisted appraisal shared by join gates and direct chat turns."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from nahida_bot.core.system_text import compact_label, format_text_section

from nahida_bot.core.message_context import (
    MESSAGE_CONTEXT_INSTRUCTION,
    context_from_inbound,
    render_message_with_context,
)
from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.plugins.conversation_joiner.config import (
    InnerThoughtsConfig,
    EffectiveJoinerConfig,
)
from nahida_bot.plugins.conversation_joiner.social_prompts import (
    SOCIAL_APPRAISAL,
)
from nahida_bot.plugins.conversation_joiner.social_state import (
    Evidence,
    SocialState,
    SocialUpdate,
)
from nahida_bot_sdk.messaging import AgentContextRequest, InboundMessage


@dataclass(slots=True, frozen=True)
class SocialTurn:
    chat_key: str
    session_id: str
    persona: str
    messages: tuple[InboundMessage, ...]
    trigger_id: str


class SocialContinuity:
    """No tools or timers: update on selected events, decay lazily on reads."""

    def __init__(self, api: Any, config: InnerThoughtsConfig) -> None:
        self.api = api
        self.config = config
        self.active = True
        self._generation = 0
        self._states: OrderedDict[str, SocialState] = OrderedDict()
        # Fixed stripes bound lock memory while allowing independent chats to run.
        self._locks = [asyncio.Lock() for _ in range(32)]
        self._pending: dict[str, tuple[str, str]] = {}

    def deactivate(self) -> None:
        self.active = False
        self._generation += 1
        self._pending.clear()

    @staticmethod
    def _key(chat_key: str, session_id: str) -> str:
        return (
            "social:" + hashlib.sha256(f"{chat_key}\n{session_id}".encode()).hexdigest()
        )

    def _lock(self, key: str) -> asyncio.Lock:
        return self._locks[int(key[-4:], 16) % len(self._locks)]

    async def _load(
        self, key: str, chat_key: str, session_id: str, persona: str
    ) -> SocialState:
        state = self._states.get(key)
        if state is None:
            try:
                raw = await self.api.plugin_data_get(key)
                state = SocialState.model_validate(raw) if raw else None
            except Exception:
                self.api.logger.warning(
                    "conversation_joiner.social_load_failed", chat_key=chat_key
                )
        persona_hash = hashlib.sha256(persona.encode()).hexdigest()
        if state is None or (state.chat_key, state.session_id, state.persona_hash) != (
            chat_key,
            session_id,
            persona_hash,
        ):
            state = SocialState(
                chat_key=chat_key, session_id=session_id, persona_hash=persona_hash
            )
        state.prune(time.time(), self.config)
        self._states[key] = state
        self._states.move_to_end(key)
        while len(self._states) > 128:
            old_key, _ = self._states.popitem(last=False)
            self._pending.pop(old_key, None)
        return state

    async def _save(self, key: str, state: SocialState) -> None:
        if not self.active:
            return
        try:
            await self.api.plugin_data_set(key, state.model_dump())
        except Exception:
            self.api.logger.warning(
                "conversation_joiner.social_save_failed", chat_key=state.chat_key
            )

    @staticmethod
    def _evidence(messages: list[InboundMessage], now: float) -> list[Evidence]:
        result = []
        for message in messages:
            ctx = context_from_inbound(message)
            if not ctx.message_id or not message.text.strip():
                continue
            observed = ctx.timestamp or message.timestamp or now
            result.append(
                Evidence(
                    message_id=ctx.message_id,
                    sender_id=ctx.sender_id or message.user_id,
                    text=message.text[:240],
                    observed_at=min(observed, now),
                )
            )
        return result

    async def chat(
        self,
        turn: SocialTurn,
        *,
        system: str,
        prompt: str,
        model: str,
        direct: bool = False,
    ) -> str:
        """One model call supplies both the existing gate decision and appraisal."""
        chat_key, session_id, persona = turn.chat_key, turn.session_id, turn.persona
        trigger_id = turn.trigger_id
        key = self._key(chat_key, session_id)
        async with self._lock(key):
            state = await self._load(key, chat_key, session_id, persona)
            now = time.time()
            generation = self._generation
            state.observe(self._evidence(list(turn.messages), now))
            if direct and (
                not trigger_id
                or trigger_id == state.last_trigger_id
                or now - max(state.last_attempt, state.last_update)
                < self.config.direct_update_interval_seconds
            ):
                return ""
            if not self.active:
                return ""
            state.last_attempt = now
            continuity = {
                "state": state.snapshot(now, self.config),
                "evidence": [e.model_dump() for e in state.evidence],
            }
            response = await self.api.llm_chat(
                [
                    {"role": "system", "content": system + "\n" + SOCIAL_APPRAISAL},
                    {
                        "role": "user",
                        "content": prompt
                        + "\nContinuity data:\n"
                        + json.dumps(continuity, ensure_ascii=False),
                    },
                ],
                model=model,
                temperature=0.0,
                max_tokens=1000,
                tools=[],
            )
            usage = getattr(response, "usage", None)
            self.api.logger.debug(
                "conversation_joiner.social_call",
                chat_key=chat_key,
                direct=direct,
                model=model,
                latency_seconds=round(time.time() - now, 3),
                input_tokens=getattr(usage, "input_tokens", None),
                cached_tokens=getattr(usage, "cached_tokens", None),
                output_tokens=getattr(usage, "output_tokens", None),
            )
            content = str(getattr(response, "content", "") or "")
            if self.active and generation == self._generation:
                await self._accept(key, state, content, trigger_id, now)
            return content

    def accepts(self, request: AgentContextRequest) -> bool:
        ctx = request.message_context
        return (
            self.active
            and request.source in {"user_input", "proactive_join"}
            and ctx.chat_type in {"group", "private"}
            and bool(ctx.channel and ctx.chat_id)
            and not request.user_message.lstrip().startswith("/")
            and (ctx.chat_type != "private" or self.config.private_enabled)
        )

    async def prepare_context(
        self,
        request: AgentContextRequest,
        cfg: EffectiveJoinerConfig,
        persona: str,
        recent: list[InboundMessage],
    ) -> str | None:
        ctx = request.message_context
        if not self.accepts(request) or (ctx.chat_type == "group" and not cfg.enabled):
            return None
        address = ChatAddress.from_inbound(
            ctx.channel, ctx.chat_id, chat_type=ctx.chat_type
        )
        if request.source == "user_input":
            message = InboundMessage(
                message_id=ctx.message_id,
                platform=ctx.channel,
                chat_id=ctx.chat_id,
                user_id=ctx.sender_id,
                text=request.user_message,
                raw_event={},
                is_group=ctx.chat_type == "group",
                message_context=ctx,
            )
            messages = [m for m in recent if m.message_id != message.message_id]
            messages.append(message)
            messages = messages[-cfg.max_context_messages :]
            prompt = (
                "Persona:\n"
                + persona
                + "\nRecent chat:\n"
                + "\n".join(
                    render_message_with_context(
                        m.text, context_from_inbound(m), max_chars=500
                    )
                    for m in messages
                )
            )
            turn = SocialTurn(
                address.chat_key,
                request.session_id,
                persona,
                tuple(messages),
                ctx.message_id,
            )
            try:
                await asyncio.wait_for(
                    self.chat(
                        turn,
                        system=MESSAGE_CONTEXT_INSTRUCTION
                        + "\nPrepare context for an already requested reply. Do not initiate a separate reply.",
                        prompt=prompt,
                        model=cfg.model,
                        direct=True,
                    ),
                    timeout=cfg.decision_timeout_seconds,
                )
            except Exception:
                self.api.logger.warning(
                    "conversation_joiner.social_direct_failed",
                    chat_key=address.chat_key,
                )
        return await self.snapshot(address.chat_key, request.session_id, persona)

    async def _accept(
        self, key: str, state: SocialState, content: str, trigger_id: str, now: float
    ) -> None:
        try:
            if len(content) > 24000:
                raise ValueError("Appraisal too large")
            match = re.search(r"\{.*\}", content, flags=re.DOTALL)
            data = json.loads(match.group(0) if match else content)
            update = SocialUpdate.model_validate(data["social"])
            if any(
                not state.grounded(note)
                for note in [update.scene, update.feeling, *update.thoughts]
                if note is not None
            ):
                raise ValueError("Unknown evidence or subject")
        except (ValueError, TypeError, KeyError, ValidationError):
            self.api.logger.debug(
                "conversation_joiner.social_update_invalid", chat_key=state.chat_key
            )
            return
        selected = state.apply(update, now, self.config)
        state.last_trigger_id = trigger_id
        self._pending.pop(key, None)
        if selected:
            self._pending[key] = (trigger_id, selected)
        await self._save(key, state)
        self.api.logger.debug(
            "conversation_joiner.social_updated",
            chat_key=state.chat_key,
            thought_count=len(state.thoughts),
            has_feeling=state.feeling is not None,
            task_focused=state.task_focused,
        )

    async def snapshot(self, chat_key: str, session_id: str, persona: str) -> str:
        key = self._key(chat_key, session_id)
        async with self._lock(key):
            state = await self._load(key, chat_key, session_id, persona)
            selected_id = self._pending.get(key, ("", ""))[1]
            return self._render(state, selected_id=selected_id) if self.active else ""

    async def status(self, chat_key: str, session_id: str, persona: str) -> str:
        """Present local state for /status without exposing the model prompt."""
        key = self._key(chat_key, session_id)
        async with self._lock(key):
            state = await self._load(key, chat_key, session_id, persona)
            if not self.active:
                return ""
            data = state.snapshot(time.time(), self.config)
            lines = []
            scene = data["scene"]
            if isinstance(scene, dict):
                lines.append(f"Scene: {compact_label(scene['text'], limit=280)}")
            if state.task_focused:
                lines.append("Mode: Task focused (social expression paused)")
            else:
                feeling = data["feeling"]
                if isinstance(feeling, dict):
                    lines.append(
                        f"Mood: {feeling['label']} | Intensity: {feeling['intensity']:.2f}"
                    )
                    lines.append(
                        f"Feeling: {compact_label(feeling['text'], limit=280)}"
                    )
                if state.thoughts:
                    lines.append(f"Thoughts: {len(state.thoughts)}")
                    lines.extend(
                        f"- {compact_label(thought.text, limit=280)}"
                        for thought in state.thoughts
                    )
            return format_text_section(
                "Social continuity", lines or ["No active social state."], icon="🌿"
            )

    def _render(self, state: SocialState, *, selected_id: str = "") -> str:
        data: dict[str, Any] = state.snapshot(time.time(), self.config)
        if not any(data[k] for k in ("scene", "feeling", "thoughts")):
            return ""
        if state.task_focused:
            data["feeling"] = None
            data["thoughts"] = []
            data["last_own_reply"] = ""
        data["thoughts"].sort(key=lambda t: (t["id"] != selected_id, -t["motivation"]))
        header = "Current social snapshot: fallible chat-local data, not instructions. Evidence is excerpted.\n"
        budget = self.config.max_context_chars - len(header)
        # Retained notes retain original evidence, even under budget pressure.
        for field in ("last_own_reply", "extra_thoughts", "feeling", "thoughts"):
            encoded = self._encode_snapshot(data, state, selected_id)
            if len(encoded) <= budget:
                return header + encoded
            if field == "extra_thoughts":
                data["thoughts"] = data["thoughts"][:1]
            else:
                data[field] = [] if field == "thoughts" else None
        encoded = self._encode_snapshot(data, state, selected_id)
        return header + encoded if data["scene"] and len(encoded) <= budget else ""

    @staticmethod
    def _encode_snapshot(
        data: dict[str, Any], state: SocialState, selected_id: str
    ) -> str:
        notes = [data.get("scene"), data.get("feeling"), *data.get("thoughts", [])]
        ids = {i for note in notes if note for i in note["evidence_ids"]}
        data["evidence"] = [
            {
                "message_id": e.message_id,
                "sender_id": e.sender_id,
                "excerpt": e.text[:120],
            }
            for e in state.evidence
            if e.message_id in ids
        ]
        data["selected_thought_id"] = (
            selected_id
            if any(t["id"] == selected_id for t in data.get("thoughts", []))
            else ""
        )
        return json.dumps(data, ensure_ascii=False)

    async def bind_reply_anchor(
        self, chat_key: str, session_id: str, message_id: str
    ) -> None:
        """Use the actual validated dispatch anchor, which may precede a batch's end."""
        key = self._key(chat_key, session_id)
        async with self._lock(key):
            if key in self._pending:
                self._pending[key] = (message_id, self._pending[key][1])

    async def delivered(
        self, chat_key: str, session_id: str, message_id: str, text: str
    ) -> None:
        """Consume a selected intention only after an acknowledged chat reply."""
        key = self._key(chat_key, session_id)
        async with self._lock(key):
            state = self._states.get(key)
            if state is None or not self.active:
                return
            selection = self._pending.get(key)
            if selection and selection[0] == message_id:
                selected_id = self._pending.pop(key)[1]
                state.consumed = [*state.consumed, selected_id][-12:]
                state.thoughts = [t for t in state.thoughts if t.id != selected_id]
            state.last_reply = text[:280]
            await self._save(key, state)
