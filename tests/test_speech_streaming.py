from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, ClassVar

import pytest

from nahida_bot.speech import (
    SpeechArtifact,
    SpeechChunk,
    SpeechRequest,
    SpeechService,
    SpeechStreamRequest,
    SpeechStreamSession,
    StreamingTtsProvider,
    TtsError,
    parse_tts_config,
)


class FakeSpeechStream(SpeechStreamSession):
    def __init__(self) -> None:
        self.text: list[tuple[str, bool]] = []
        self.cancelled = False
        self.closed = False

    async def push_text(self, text: str, *, commit: bool = True) -> None:
        self.text.append((text, commit))

    async def finish_input(self) -> None:
        return None

    async def _chunks(self) -> AsyncIterator[SpeechChunk]:
        yield SpeechChunk(
            data=b"pcm",
            sequence=0,
            sample_rate_hz=24_000,
            duration_ms=20,
            final=True,
        )

    def __aiter__(self) -> AsyncIterator[SpeechChunk]:
        return self._chunks()

    async def cancel(self) -> None:
        self.cancelled = True

    async def close(self) -> None:
        self.closed = True


class FakeStreamingProvider(StreamingTtsProvider):
    type: ClassVar[str] = "fake-streaming"
    last_instance: ClassVar[FakeStreamingProvider | None] = None

    @staticmethod
    def parse_backend_config(raw: dict[str, Any]) -> dict[str, Any]:
        return raw

    @staticmethod
    def parse_voice_config(raw: dict[str, Any]) -> dict[str, Any]:
        return raw

    def __init__(self, backend_config: Any, *, http_client: Any = None) -> None:
        del http_client
        self.backend_config = backend_config
        self.request: SpeechStreamRequest | None = None
        self.voice_config: Any = None
        self.stream = FakeSpeechStream()
        self.closed = False
        FakeStreamingProvider.last_instance = self

    async def synthesize(
        self, request: SpeechRequest, voice_config: Any
    ) -> SpeechArtifact:
        del request, voice_config
        return SpeechArtifact(data=b"whole", mime_type="audio/wav")

    async def open_stream(
        self,
        request: SpeechStreamRequest,
        voice_config: Any,
    ) -> SpeechStreamSession:
        self.request = request
        self.voice_config = voice_config
        return self.stream

    async def close(self) -> None:
        self.closed = True


def _streaming_config():
    return parse_tts_config(
        {
            "default_backend": "live",
            "backends": {"live": {"type": "fake-streaming", "endpoint": "local"}},
            "voices": {"nahida": {"backend": "live", "profile": "nahida-v1"}},
            "default_voice": "nahida",
        }
    )


@pytest.mark.asyncio
async def test_service_opens_and_attributes_streaming_provider() -> None:
    service = SpeechService(
        _streaming_config(),
        providers={FakeStreamingProvider.type: FakeStreamingProvider},
    )
    try:
        opened = await service.open_stream(style="soft", speed=0.9)
        instance = FakeStreamingProvider.last_instance
        assert instance is not None
        assert opened.session is instance.stream
        assert opened.provider == "fake-streaming"
        assert opened.backend == "live"
        assert opened.voice == "nahida"
        assert instance.request == SpeechStreamRequest(
            text_lang="",
            style="soft",
            speed=0.9,
            pitch=0.0,
            output_format="audio/pcm",
        )
        assert instance.voice_config["profile"] == "nahida-v1"
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_artifact_only_provider_fails_streaming_explicitly() -> None:
    config = parse_tts_config(
        {
            "default_backend": "legacy",
            "backends": {
                "legacy": {
                    "type": "gpt-sovits-v2",
                    "base_url": "http://127.0.0.1:9880",
                }
            },
            "voices": {
                "nahida": {
                    "backend": "legacy",
                    "ref_audio_path": "/voice/nahida.wav",
                    "prompt_text": "你好",
                }
            },
            "default_voice": "nahida",
        }
    )
    service = SpeechService(config)
    try:
        with pytest.raises(TtsError) as exc_info:
            await service.open_stream()
    finally:
        await service.close()

    assert exc_info.value.code == "tts_streaming_unsupported"
    assert exc_info.value.backend == "legacy"
    assert exc_info.value.provider == "gpt-sovits-v2"
