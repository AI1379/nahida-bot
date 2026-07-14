"""Configuration for the unified TTS service.

``backends`` and ``voices`` are intentionally raw dicts: each provider adapter
parses its own sub-config from them (keyed on ``type``), so adding a new TTS
backend type does not require touching this module.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TtsConfig(BaseModel):
    """Top-level ``tts:`` configuration for the unified speech service."""

    model_config = ConfigDict(frozen=True, extra="allow")

    default_backend: str = "default"
    # Raw per-backend config dicts; each must carry a ``type`` discriminator.
    backends: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # Raw per-voice config dicts; provider-specific. May carry a ``backend`` ref.
    voices: dict[str, dict[str, Any]] = Field(default_factory=dict)
    default_voice: str = ""

    # Bot-side behaviour (provider-agnostic; enforced by the speak plugin).
    output_dir: str = "generated/audio"
    auto_send: bool = True
    command_names: list[str] = Field(default_factory=lambda: ["speak", "说话"])
    attachment_type: str = "voice"  # voice | audio
    max_text_length: int = Field(default=300, ge=1)
    max_calls_per_24h: int = Field(default=0, ge=0)
    max_concurrency: int = Field(default=1, ge=1, le=16)

    def backend_raw(self, name: str = "") -> tuple[str, dict[str, Any]]:
        """Return ``(backend_name, raw)`` for the selected backend.

        Raises ``ValueError`` if the backend is missing or lacks a ``type``.
        """

        backend_name = name.strip() or self.default_backend
        if backend_name not in self.backends:
            available = ", ".join(sorted(self.backends)) or "(none)"
            raise ValueError(
                f"TTS backend '{backend_name}' is not configured. "
                f"Available: {available}"
            )
        raw = self.backends[backend_name]
        if not isinstance(raw, dict) or not str(raw.get("type", "")).strip():
            raise ValueError(
                f"TTS backend '{backend_name}' is missing a 'type' discriminator."
            )
        return backend_name, raw

    def resolve_voice(self, voice_name: str = "") -> tuple[str, str, dict[str, Any]]:
        """Resolve a voice name to ``(voice_name, backend_name, raw)``.

        Selection order: exact name → ``default_voice`` → the only configured
        voice. The backend is taken from the voice's optional ``backend`` field,
        falling back to ``default_backend``.
        """

        name = voice_name.strip()
        if name and name in self.voices:
            raw = self.voices[name]
        elif self.default_voice and self.default_voice in self.voices:
            name = self.default_voice
            raw = self.voices[name]
        elif len(self.voices) == 1:
            name = next(iter(self.voices))
            raw = self.voices[name]
        else:
            available = ", ".join(sorted(self.voices)) or "(none)"
            raise ValueError(
                f"TTS voice '{name or '(empty)'}' is not configured. "
                f"Configure voices/default_voice. Available: {available}"
            )
        if not isinstance(raw, dict):
            raise ValueError(f"TTS voice '{name}' config must be a mapping.")
        backend_name = str(raw.get("backend", "")).strip() or self.default_backend
        return name, backend_name, raw


def parse_tts_config(raw: dict[str, Any]) -> TtsConfig:
    """Parse raw manifest/config into a typed :class:`TtsConfig`."""

    return TtsConfig(**raw)
