"""Unified TTS service (see docs/design/desktop-app.md §9.3.1, part A)."""

from nahida_bot.speech.base import SpeechArtifact, SpeechRequest, TtsError, TtsProvider
from nahida_bot.speech.config import TtsConfig, parse_tts_config
from nahida_bot.speech.service import SpeechService

__all__ = [
    "SpeechArtifact",
    "SpeechRequest",
    "TtsError",
    "TtsProvider",
    "TtsConfig",
    "parse_tts_config",
    "SpeechService",
]
