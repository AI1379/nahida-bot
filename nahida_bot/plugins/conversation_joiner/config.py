"""Configuration models for the conversation joiner plugin."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JoinerPrefilterConfig(BaseModel):
    """Cheap prefilter knobs before the secretary model is called."""

    model_config = ConfigDict(frozen=True, extra="allow")

    ignore_commands: bool = True
    ignore_mentions: bool = True
    min_text_chars: int = Field(default=4, ge=0)
    keyword_hints: list[str] = Field(default_factory=list)
    sample_rate: float = Field(default=0.15, ge=0.0, le=1.0)
    keyword_sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)


class PersonaContextConfig(BaseModel):
    """Workspace persona files injected into the secretary prompt."""

    model_config = ConfigDict(frozen=True, extra="allow")

    enabled: bool = True
    files: list[str] = Field(default_factory=lambda: ["SOUL.md"])
    max_chars: int = Field(default=4000, ge=0)
    cache_ttl_seconds: float = Field(default=60.0, ge=0.0)


class GroupJoinerConfig(BaseModel):
    """Optional per-group overrides keyed by typed chat key."""

    model_config = ConfigDict(frozen=True, extra="allow")

    enabled: bool | None = None
    model: str | None = None
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    max_context_messages: int | None = Field(default=None, ge=1)
    max_context_chars: int | None = Field(default=None, ge=100)
    cooldown_seconds: float | None = Field(default=None, ge=0.0)
    max_triggers_per_hour: int | None = Field(default=None, ge=0)
    debounce_seconds: float | None = Field(default=None, ge=0.0)
    decision_timeout_seconds: float | None = Field(default=None, ge=0.1)


class ConversationJoinerConfig(BaseModel):
    """Top-level conversation joiner configuration."""

    model_config = ConfigDict(frozen=True, extra="allow")

    enabled: bool = False
    model: str = "cheap"
    threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    max_context_messages: int = Field(default=12, ge=1)
    max_context_chars: int = Field(default=3000, ge=100)
    cooldown_seconds: float = Field(default=300.0, ge=0.0)
    max_triggers_per_hour: int = Field(default=3, ge=0)
    debounce_seconds: float = Field(default=20.0, ge=0.0)
    decision_timeout_seconds: float = Field(default=8.0, ge=0.1)
    prefilter: JoinerPrefilterConfig = Field(default_factory=JoinerPrefilterConfig)
    persona_context: PersonaContextConfig = Field(default_factory=PersonaContextConfig)
    groups: dict[str, GroupJoinerConfig] = Field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class EffectiveJoinerConfig:
    """Resolved group-specific runtime config."""

    enabled: bool
    model: str
    threshold: float
    max_context_messages: int
    max_context_chars: int
    cooldown_seconds: float
    max_triggers_per_hour: int
    debounce_seconds: float
    decision_timeout_seconds: float
    prefilter: JoinerPrefilterConfig
    persona_context: PersonaContextConfig


def parse_conversation_joiner_config(raw: dict[str, Any]) -> ConversationJoinerConfig:
    """Parse raw manifest config into a typed config object."""
    return ConversationJoinerConfig(**raw)


def effective_group_config(
    config: ConversationJoinerConfig,
    chat_key: str,
) -> EffectiveJoinerConfig:
    """Resolve global defaults plus optional per-group overrides."""
    group = config.groups.get(chat_key)
    return EffectiveJoinerConfig(
        enabled=_coalesce(group.enabled if group else None, config.enabled),
        model=_coalesce(group.model if group else None, config.model),
        threshold=_coalesce(group.threshold if group else None, config.threshold),
        max_context_messages=_coalesce(
            group.max_context_messages if group else None,
            config.max_context_messages,
        ),
        max_context_chars=_coalesce(
            group.max_context_chars if group else None,
            config.max_context_chars,
        ),
        cooldown_seconds=_coalesce(
            group.cooldown_seconds if group else None,
            config.cooldown_seconds,
        ),
        max_triggers_per_hour=_coalesce(
            group.max_triggers_per_hour if group else None,
            config.max_triggers_per_hour,
        ),
        debounce_seconds=_coalesce(
            group.debounce_seconds if group else None,
            config.debounce_seconds,
        ),
        decision_timeout_seconds=_coalesce(
            group.decision_timeout_seconds if group else None,
            config.decision_timeout_seconds,
        ),
        prefilter=config.prefilter,
        persona_context=config.persona_context,
    )


def _coalesce[T](value: T | None, fallback: T) -> T:
    return fallback if value is None else value
