"""Provider-neutral contracts for the realtime voice input data plane."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

TranscriptEventType = Literal[
    "speech_started",
    "speech_stopped",
    "partial",
    "final",
]


@dataclass(slots=True, frozen=True)
class AudioFrame:
    """One ordered PCM frame captured from a realtime transport."""

    data: bytes
    sequence: int
    captured_at_ms: int
    sample_rate_hz: int = 16_000
    channels: int = 1
    sample_width_bytes: int = 2


@dataclass(slots=True, frozen=True)
class AsrStreamRequest:
    """Stable settings for one streaming recognition session."""

    language: str = "zh"
    sample_rate_hz: int = 16_000
    channels: int = 1
    hotwords: tuple[str, ...] = ()
    partial_results: bool = True


@dataclass(slots=True, frozen=True)
class TranscriptEvent:
    """One revisable or final recognition/turn-detection event."""

    type: TranscriptEventType
    text: str = ""
    revision: int = 0
    audio_start_ms: int = 0
    audio_end_ms: int = 0
    confidence: float | None = None


class StreamingAsrSession(ABC):
    """Long-lived input stream that emits turn and transcript events."""

    @abstractmethod
    async def push_audio(self, frame: AudioFrame) -> None:
        """Append one ordered PCM frame."""

    @abstractmethod
    async def finish_input(self) -> None:
        """Flush pending recognition after the transport ends normally."""

    @abstractmethod
    def __aiter__(self) -> AsyncIterator[TranscriptEvent]:
        """Iterate recognition and speech-boundary events."""

    @abstractmethod
    async def cancel(self) -> None:
        """Cancel recognition promptly and invalidate queued events."""

    @abstractmethod
    async def close(self) -> None:
        """Release resources owned by this stream."""


class StreamingAsrProvider(ABC):
    """Factory for provider-specific streaming ASR sessions."""

    type: str

    @abstractmethod
    async def open_stream(self, request: AsrStreamRequest) -> StreamingAsrSession:
        """Open one recognition stream."""


__all__ = [
    "AsrStreamRequest",
    "AudioFrame",
    "StreamingAsrProvider",
    "StreamingAsrSession",
    "TranscriptEvent",
    "TranscriptEventType",
]
