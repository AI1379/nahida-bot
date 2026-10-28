"""Grounded social state with wall-clock expiry, independent of LLM and I/O."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from nahida_bot.plugins.conversation_joiner.config import InnerThoughtsConfig


class Evidence(BaseModel):
    message_id: str
    sender_id: str
    text: str
    observed_at: float


class Note(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=280)
    subject_id: str = Field(default="", max_length=128)
    evidence_ids: list[str] = Field(min_length=1, max_length=4)


class Feeling(Note):
    label: str = Field(min_length=1, max_length=32)
    intensity: float = Field(ge=0, le=1, allow_inf_nan=False)


class Thought(Note):
    motivation: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    intent: Literal["ask", "share", "acknowledge", "play", "reply", "wait"] = "reply"

    @property
    def id(self) -> str:
        encoded = json.dumps(
            self.model_dump(exclude={"motivation"}), sort_keys=True, ensure_ascii=False
        )
        return hashlib.sha256(encoded.encode()).hexdigest()[:16]


class SocialUpdate(BaseModel):
    """Null scene/feeling retains prior state; thoughts is the complete live set."""

    model_config = ConfigDict(extra="ignore")

    scene: Note | None = None
    clear_scene: bool = False
    feeling: Feeling | None = None
    clear_feeling: bool = False
    thoughts: list[Thought] = Field(max_length=3)
    express: int | None = Field(default=None, ge=0, le=2)
    task_focused: bool = False


class SocialState(BaseModel):
    version: Literal[1] = 1
    session_id: str
    chat_key: str
    persona_hash: str
    scene: Note | None = None
    feeling: Feeling | None = None
    thoughts: list[Thought] = Field(default_factory=list, max_length=3)
    evidence: list[Evidence] = Field(default_factory=list, max_length=40)
    consumed: list[str] = Field(default_factory=list, max_length=12)
    last_update: float = 0
    last_attempt: float = 0
    last_trigger_id: str = ""
    last_reply: str = ""
    task_focused: bool = False

    def evidence_time(self, note: Note) -> float:
        by_id = {item.message_id: item for item in self.evidence}
        return max(
            (by_id[i].observed_at for i in note.evidence_ids if i in by_id), default=0
        )

    def grounded(self, note: Note) -> bool:
        by_id = {item.message_id: item for item in self.evidence}
        if not all(i in by_id for i in note.evidence_ids):
            return False
        # Model-generated names cannot become account identities.
        return (
            not note.subject_id
            or note.subject_id == "self"
            or any(by_id[i].sender_id == note.subject_id for i in note.evidence_ids)
        )

    def prune(self, now: float, cfg: InnerThoughtsConfig) -> None:
        def alive(note: Note | None, ttl: float) -> bool:
            return (
                note is not None
                and self.grounded(note)
                and now - self.evidence_time(note) < ttl
            )

        if not alive(self.scene, cfg.state_ttl_seconds):
            self.scene = None
        if not alive(self.feeling, cfg.state_ttl_seconds):
            self.feeling = None
        self.thoughts = [
            t
            for t in self.thoughts
            if alive(t, cfg.thought_ttl_seconds) and t.id not in self.consumed
        ]
        self.evidence = [
            e for e in self.evidence if now - e.observed_at < cfg.state_ttl_seconds
        ]
        if now - self.last_update >= cfg.state_ttl_seconds:
            self.last_reply = ""
            self.task_focused = False

    def observe(self, items: list[Evidence]) -> None:
        # Keep the original timestamp when a rolling window repeats evidence.
        by_id = {item.message_id: item for item in self.evidence}
        for item in items:
            by_id.setdefault(item.message_id, item)
        needed = {
            i
            for note in [self.scene, self.feeling, *self.thoughts]
            if note
            for i in note.evidence_ids
        }
        ordered = sorted(by_id.values(), key=lambda e: e.observed_at)
        retained = [e for e in ordered if e.message_id in needed]
        recent = [e for e in ordered if e.message_id not in needed][-24:]
        self.evidence = sorted([*retained, *recent], key=lambda e: e.observed_at)[-40:]

    def apply(self, update: SocialUpdate, now: float, cfg: InnerThoughtsConfig) -> str:
        notes = [update.scene, update.feeling, *update.thoughts]
        if any(not self.grounded(note) for note in notes if note is not None):
            return ""
        if update.clear_scene:
            self.scene = None
        elif update.scene is not None and self.grounded(update.scene):
            self.scene = update.scene
        if update.clear_feeling:
            self.feeling = None
        elif update.feeling is not None and self.grounded(update.feeling):
            self.feeling = update.feeling
        self.thoughts = [t for t in update.thoughts if self.grounded(t)]
        self.task_focused = update.task_focused
        self.last_update = now
        self.prune(now, cfg)
        if update.express is not None and update.express < len(update.thoughts):
            selected = update.thoughts[update.express]
            if selected in self.thoughts:
                return selected.id
        return ""

    def snapshot(self, now: float, cfg: InnerThoughtsConfig) -> dict[str, object]:
        self.prune(now, cfg)
        feeling = self.feeling.model_dump() if self.feeling else None
        if feeling is not None and self.feeling is not None:
            elapsed = max(0, now - self.evidence_time(self.feeling))
            feeling["intensity"] = round(
                self.feeling.intensity * 0.5 ** (elapsed / cfg.mood_half_life_seconds),
                2,
            )
            if feeling["intensity"] < 0.05:
                feeling = None
        return {
            "scene": self.scene.model_dump() if self.scene else None,
            "feeling": feeling,
            "thoughts": [{"id": t.id, **t.model_dump()} for t in self.thoughts],
            "task_focused": self.task_focused,
            "last_own_reply": self.last_reply,
        }
