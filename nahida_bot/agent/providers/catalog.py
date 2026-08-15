"""Curated provider templates for interactive configuration surfaces.

Single source of truth for "what a sensible ``providers:`` entry looks like"
per provider type. Consumed by the CLI (``bootstrap``, ``auth login``) so
both commands stamp identical entries instead of drifting copies, and
available to future surfaces (doctor hints, WebUI provider-add UI).

Pure data plus rendering — no I/O, no prompting, no CLI imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from nahida_bot.agent.providers.registry import list_providers


@dataclass(slots=True, frozen=True)
class ProviderTemplate:
    """A canned provider definition interactive flows can stamp out."""

    label: str
    provider_type: str
    base_url: str = ""
    key_env: str = ""  # suggested env var name; empty for OAuth-only types
    key_label: str = ""
    models: list[dict[str, Any]] = field(default_factory=list)
    needs_key: bool = True
    # Generic relays have no canned endpoint; the user must supply one.
    base_url_required: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def render_entry(self) -> dict[str, Any]:
        """Return the ``providers.<id>`` mapping for this template.

        ``api_key`` references the suggested env var with an empty default so
        the entry validates even before any secret exists; the auth CLI stores
        credentials in SQLite, which takes priority over this placeholder.
        """
        entry: dict[str, Any] = {"type": self.provider_type}
        if self.needs_key:
            entry["api_key"] = f"${{{self.key_env or 'LLM_API_KEY'}:}}"
        if self.base_url:
            entry["base_url"] = self.base_url
        entry.update(self.extra)
        entry["models"] = list(self.models)
        return entry


# Curated presets keyed by menu slug. ``provider_type`` values mirror the
# adapters registered in ``nahida_bot.agent.providers``.
PROVIDER_PRESETS: dict[str, ProviderTemplate] = {
    "deepseek": ProviderTemplate(
        label="DeepSeek (official)",
        provider_type="deepseek",
        base_url="https://api.deepseek.com",
        key_env="DEEPSEEK_LLM_API_KEY",
        key_label="DeepSeek API key",
        models=[{"name": "deepseek-chat", "tags": ["primary"]}],
    ),
    "siliconflow": ProviderTemplate(
        label="SiliconFlow (OpenAI-compatible)",
        provider_type="openai-compatible",
        base_url="https://api.siliconflow.cn/v1",
        key_env="SILICONFLOW_LLM_API_KEY",
        key_label="SiliconFlow API key",
        models=[{"name": "Qwen/Qwen3.6-35B-A3B", "tags": ["primary", "vision"]}],
        extra={"merge_system_messages": True, "stream_responses": True},
    ),
    "openai": ProviderTemplate(
        label="OpenAI (Responses API)",
        provider_type="openai-responses",
        base_url="https://api.openai.com/v1",
        key_env="OPENAI_API_KEY",
        key_label="OpenAI API key",
        models=[{"name": "gpt-5.4", "tags": ["primary"]}],
        extra={"stream_responses": True},
    ),
    "anthropic": ProviderTemplate(
        label="Anthropic Claude",
        provider_type="anthropic",
        base_url="",
        key_env="ANTHROPIC_API_KEY",
        key_label="Anthropic API key",
        models=[{"name": "claude-sonnet-4-5", "tags": ["primary"]}],
    ),
    "glm": ProviderTemplate(
        label="GLM / ZhiPu",
        provider_type="glm",
        base_url="",
        key_env="GLM_API_KEY",
        key_label="GLM API key",
        models=[{"name": "glm-4-plus", "tags": ["primary"]}],
    ),
    "minimax": ProviderTemplate(
        label="MiniMax (Anthropic-compatible)",
        provider_type="minimax",
        base_url="https://api.minimaxi.com/anthropic",
        key_env="MINIMAX_LLM_API_KEY",
        key_label="MiniMax API key",
        models=[{"name": "MiniMax-M3", "tags": ["primary", "vision"]}],
        extra={"stream_responses": True},
    ),
    "groq": ProviderTemplate(
        label="Groq (OpenAI-compatible)",
        provider_type="groq",
        base_url="https://api.groq.com/openai/v1",
        key_env="GROQ_API_KEY",
        key_label="Groq API key",
        models=[{"name": "llama-3.3-70b-versatile", "tags": ["primary"]}],
    ),
    "codex": ProviderTemplate(
        label="ChatGPT Codex (Plus/Pro subscription)",
        provider_type="codex",
        base_url="",
        key_env="",
        key_label="",
        models=[{"name": "gpt-5.5"}, {"name": "gpt-5.4-mini"}],
        needs_key=False,
        extra={"stream_responses": True},
    ),
    "generic-openai": ProviderTemplate(
        label="Generic OpenAI-compatible (custom base_url)",
        provider_type="openai-compatible",
        base_url="",
        key_env="LLM_API_KEY",
        key_label="API key",
        models=[{"name": "gpt-3.5-turbo", "tags": ["primary"]}],
        base_url_required=True,
        extra={"stream_responses": True},
    ),
}


def preset_for_type(provider_type: str) -> ProviderTemplate | None:
    """Return the preset whose ``provider_type`` matches, if any.

    Powers the fast path where the requested provider id itself names a known
    type (``auth login codex``): the type is implied, no menu needed.
    """
    for preset in PROVIDER_PRESETS.values():
        if preset.provider_type == provider_type:
            return preset
    return None


def is_known_provider_type(provider_type: str) -> bool:
    """True when *provider_type* is registered by a built-in adapter."""

    return any(d.provider_type == provider_type for d in list_providers())


def preset_with_base_url(preset: ProviderTemplate, base_url: str) -> ProviderTemplate:
    """Return a copy of *preset* with ``base_url`` overridden."""

    return replace(preset, base_url=base_url)
