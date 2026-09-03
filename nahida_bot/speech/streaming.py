"""Optional streaming TTS contracts for realtime voice sessions.

The existing :mod:`nahida_bot.speech.base` contract intentionally models
whole-artifact synthesis.  Realtime voice needs a different lifetime: the
provider stays warm, accepts committed text incrementally, yields timestamped
audio chunks, and can be cancelled immediately.  Keeping this as an optional
provider capability lets artifact-only backends such as GPT-SoVITS continue to
work without pretending that chunking a completed file is realtime synthesis.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from nahida_bot.speech.base import TtsProvider


@dataclass(slots=True, frozen=True)
class SpeechStreamRequest:
    """Provider-agnostic settings for one long-lived synthesis stream."""

    text_lang: str = "zh"
    style: str = ""
    speed: float = 0.0
    pitch: float = 0.0
    output_format: str = "audio/pcm"


@dataclass(slots=True, frozen=True)
class SpeechChunk:
    """One ordered audio chunk produced by a streaming TTS session.

    ``text_start`` and ``text_end`` refer to offsets in the concatenation of
    text committed through :meth:`SpeechStreamSession.push_text`.  Providers
    that cannot align audio to text leave both at zero; playback code must then
    avoid claiming precise partial-text delivery.
    """

    data: bytes
    sequence: int
    sample_rate_hz: int
    channels: int = 1
    sample_width_bytes: int = 2
    mime_type: str = "audio/pcm"
    duration_ms: int = 0
    text_start: int = 0
    text_end: int = 0
    final: bool = False


class SpeechStreamSession(ABC):
    """Bidirectional lifetime for one realtime TTS response."""

    @abstractmethod
    async def push_text(self, text: str, *, commit: bool = True) -> None:
        """Append stable text to the stream.

        ``commit=False`` is reserved for providers that support revisable text.
        Callers must use committed text for providers that do not document that
        capability.
        """

    @abstractmethod
    async def finish_input(self) -> None:
        """Signal that no more text will be appended and flush final audio."""

    @abstractmethod
    def __aiter__(self) -> AsyncIterator[SpeechChunk]:
        """Iterate audio chunks until finalization or cancellation."""

    @abstractmethod
    async def cancel(self) -> None:
        """Stop generation promptly and invalidate queued provider output."""

    @abstractmethod
    async def close(self) -> None:
        """Release resources owned by this stream."""


class StreamingTtsProvider(TtsProvider, ABC):
    """Optional :class:`TtsProvider` capability for true incremental audio."""

    @abstractmethod
    async def open_stream(
        self,
        request: SpeechStreamRequest,
        voice_config: Any,
    ) -> SpeechStreamSession:
        """Open a warm synthesis stream for one assistant response."""


@dataclass(slots=True, frozen=True)
class OpenedSpeechStream:
    """Resolved stream plus stable attribution used by telemetry and memory."""

    session: SpeechStreamSession
    provider: str
    backend: str
    voice: str


__all__ = [
    "OpenedSpeechStream",
    "SpeechChunk",
    "SpeechStreamRequest",
    "SpeechStreamSession",
    "StreamingTtsProvider",
]
