"""Unified TTS service (see docs/design/desktop-app.md §9.3.1, part A)."""

from nahida_bot.speech.base import SpeechArtifact, SpeechRequest, TtsError, TtsProvider
from nahida_bot.speech.config import TtsConfig, parse_tts_config
from nahida_bot.speech.service import SpeechService
from nahida_bot.speech.streaming import (
    OpenedSpeechStream,
    SpeechChunk,
    SpeechStreamRequest,
    SpeechStreamSession,
    StreamingTtsProvider,
)

__all__ = [
    "OpenedSpeechStream",
    "SpeechArtifact",
    "SpeechChunk",
    "SpeechRequest",
    "SpeechStreamRequest",
    "SpeechStreamSession",
    "StreamingTtsProvider",
    "TtsError",
    "TtsProvider",
    "TtsConfig",
    "parse_tts_config",
    "SpeechService",
]
