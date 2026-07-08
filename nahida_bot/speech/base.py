"""Unified TTS abstraction.

See ``docs/design/desktop-app.md`` §9.3.1 for the full SpeechService vision.
This module implements part A (provider registry + swappable backends); the
ArtifactStore / Gateway Media / Desktop playback plumbing (part B) is deferred
to Desktop Phase 7.

A ``TtsProvider`` adapter wraps one backend type (e.g. GPT-SoVITS api_v2,
future edge-tts / IndexTTS / cloud). The :class:`SpeechService` holds a
``type`` → adapter-class registry and dispatches per ``tts.backends.<name>.type``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

import httpx


@dataclass(slots=True, frozen=True)
class SpeechRequest:
    """Provider-agnostic synthesis request."""

    text: str
    text_lang: str = "zh"
    style: str = ""  # optional style/emotion hint; provider may ignore
    speed: float = 0.0  # 0.0 = unset → provider falls back to its backend default
    pitch: float = 0.0
    output_format: str = ""  # mime hint (e.g. "audio/wav"); empty → provider default


@dataclass(slots=True, frozen=True)
class SpeechArtifact:
    """One synthesized audio clip.

    Part B will add ``artifact_id`` / ``download_url`` / ``expires_at`` for the
    ArtifactStore + Gateway Media API. For now Channel consumers read ``data``
    directly and send it as a voice attachment.
    """

    data: bytes
    mime_type: str
    duration_ms: int = 0
    provider: str = ""
    voice: str = ""


class TtsError(Exception):
    """Raised when TTS synthesis fails in a user-facing way."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        provider: str = "",
        backend: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.provider = provider
        self.backend = backend


class TtsProvider(ABC):
    """Adapter contract for one TTS backend type.

    Subclasses set ``type`` (the discriminator matched against
    ``tts.backends.<name>.type``) and implement config parsing, synthesis and
    close. The constructor signature is fixed so the :class:`SpeechService`
    registry can instantiate any adapter uniformly.
    """

    type: ClassVar[str]

    @staticmethod
    @abstractmethod
    def parse_backend_config(raw: dict[str, Any]) -> Any:
        """Validate/parse the backend config sub-dict into a typed model."""

    @staticmethod
    @abstractmethod
    def parse_voice_config(raw: dict[str, Any]) -> Any:
        """Validate/parse one ``tts.voices.<name>`` sub-dict into a typed model."""

    @abstractmethod
    def __init__(
        self,
        backend_config: Any,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Construct the adapter from its parsed backend config."""

    @abstractmethod
    async def synthesize(
        self, request: SpeechRequest, voice_config: Any
    ) -> SpeechArtifact:
        """Synthesize one clip. Raises :class:`TtsError` on failure."""

    @abstractmethod
    async def close(self) -> None:
        """Release the underlying HTTP client if owned."""
