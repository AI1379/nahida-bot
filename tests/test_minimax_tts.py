"""Tests for the MiniMax synchronous T2A provider."""

from __future__ import annotations

import json

import httpx
import pytest

from nahida_bot.speech.base import SpeechRequest, TtsError
from nahida_bot.speech.config import parse_tts_config
from nahida_bot.speech.providers.minimax import (
    MiniMaxTtsBackendConfig,
    MiniMaxTtsProvider,
    MiniMaxTtsVoice,
)
from nahida_bot.speech.service import SpeechService

MP3_BYTES = b"ID3\x04\x00\x00\x00\x00\x00\x00minimax-test"


def _success_response(
    *,
    audio: bytes = MP3_BYTES,
    audio_format: str = "mp3",
    duration_ms: object = 4980,
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "data": {"audio": audio.hex(), "status": 2},
            "extra_info": {
                "audio_length": duration_ms,
                "audio_format": audio_format,
                "usage_characters": 34,
            },
            "base_resp": {"status_code": 0, "status_msg": "success"},
        },
    )


def _provider_with_transport(
    handler,
    *,
    backend: MiniMaxTtsBackendConfig | None = None,
) -> tuple[MiniMaxTtsProvider, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = MiniMaxTtsProvider(
        backend
        or MiniMaxTtsBackendConfig(
            base_url="https://minimax.example",
            api_key="test-key",
        ),
        http_client=http,
    )
    return provider, http


def _voice(**overrides: object) -> MiniMaxTtsVoice:
    raw: dict[str, object] = {"voice_id": "NahidaDesktopTest_12345678"}
    raw.update(overrides)
    return MiniMaxTtsVoice(**raw)


@pytest.mark.asyncio
async def test_minimax_synthesize_decodes_audio_and_builds_payload() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return _success_response()

    provider, http = _provider_with_transport(handler)
    try:
        artifact = await provider.synthesize(
            SpeechRequest(text=" 你好呀 ", text_lang="zh"),
            _voice(),
        )
    finally:
        await http.aclose()

    assert artifact.data == MP3_BYTES
    assert artifact.mime_type == "audio/mpeg"
    assert artifact.duration_ms == 4980
    assert artifact.provider == "minimax-t2a-v2"
    assert captured["url"] == "https://minimax.example/v1/t2a_v2"
    assert captured["authorization"] == "Bearer test-key"

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "speech-2.8-hd"
    assert body["text"] == "你好呀"
    assert body["stream"] is False
    assert body["language_boost"] == "Chinese"
    assert body["output_format"] == "hex"
    assert body["voice_setting"] == {
        "voice_id": "NahidaDesktopTest_12345678",
        "speed": 1.0,
        "vol": 1.0,
        "pitch": 0,
    }
    assert body["audio_setting"] == {
        "sample_rate": 32000,
        "bitrate": 128000,
        "format": "mp3",
        "channel": 1,
    }


@pytest.mark.asyncio
async def test_minimax_request_overrides_voice_controls_and_output_format() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _success_response(audio=b"RIFFdata", audio_format="wav")

    provider, http = _provider_with_transport(handler)
    try:
        artifact = await provider.synthesize(
            SpeechRequest(
                text="晚安",
                text_lang="ja",
                style="whisper",
                speed=1.25,
                pitch=2.6,
                output_format="audio/wav",
            ),
            _voice(
                speed=0.8,
                volume=1.2,
                pitch=-2,
                emotion="calm",
                language_boost="Korean",
            ),
        )
    finally:
        await http.aclose()

    assert artifact.mime_type == "audio/wav"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["language_boost"] == "Korean"
    assert body["audio_setting"]["format"] == "wav"
    assert body["voice_setting"] == {
        "voice_id": "NahidaDesktopTest_12345678",
        "speed": 1.25,
        "vol": 1.2,
        "pitch": 3,
        "emotion": "whipser",
    }


@pytest.mark.asyncio
async def test_minimax_ignores_unknown_request_style() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _success_response()

    provider, http = _provider_with_transport(handler)
    try:
        await provider.synthesize(
            SpeechRequest(text="你好", style="playful"),
            _voice(emotion="calm"),
        )
    finally:
        await http.aclose()

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["voice_setting"]["emotion"] == "calm"


@pytest.mark.asyncio
async def test_minimax_extra_body_cannot_override_required_contract_fields() -> None:
    captured: dict[str, object] = {}
    backend = MiniMaxTtsBackendConfig(
        base_url="https://minimax.example",
        api_key="test-key",
        extra_body={"stream": True, "text": "wrong", "custom": "kept"},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _success_response()

    provider, http = _provider_with_transport(handler, backend=backend)
    try:
        await provider.synthesize(SpeechRequest(text="right"), _voice())
    finally:
        await http.aclose()

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["stream"] is False
    assert body["text"] == "right"
    assert body["custom"] == "kept"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_code", "retryable"),
    [
        (1002, "tts_rate_limited", True),
        (1004, "tts_auth_failed", False),
        (1008, "tts_quota_exceeded", False),
        (1033, "tts_server_error", True),
        (2056, "tts_quota_exceeded", False),
    ],
)
async def test_minimax_maps_api_errors(
    status_code: int,
    expected_code: str,
    retryable: bool,
) -> None:
    provider, http = _provider_with_transport(
        lambda request: httpx.Response(
            200,
            json={
                "base_resp": {
                    "status_code": status_code,
                    "status_msg": "rejected",
                }
            },
        )
    )
    try:
        with pytest.raises(TtsError) as exc_info:
            await provider.synthesize(SpeechRequest(text="你好"), _voice())
    finally:
        await http.aclose()

    assert exc_info.value.code == expected_code
    assert exc_info.value.retryable is retryable
    assert str(status_code) in exc_info.value.message


@pytest.mark.asyncio
async def test_minimax_maps_http_server_error_even_when_body_is_not_json() -> None:
    provider, http = _provider_with_transport(
        lambda request: httpx.Response(502, text="bad gateway")
    )
    try:
        with pytest.raises(TtsError) as exc_info:
            await provider.synthesize(SpeechRequest(text="你好"), _voice())
    finally:
        await http.aclose()

    assert exc_info.value.code == "tts_server_error"
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_minimax_preserves_api_error_category_on_http_error() -> None:
    provider, http = _provider_with_transport(
        lambda request: httpx.Response(
            400,
            json={
                "base_resp": {
                    "status_code": 2056,
                    "status_msg": "plan exhausted",
                }
            },
        )
    )
    try:
        with pytest.raises(TtsError) as exc_info:
            await provider.synthesize(SpeechRequest(text="你好"), _voice())
    finally:
        await http.aclose()

    assert exc_info.value.code == "tts_quota_exceeded"
    assert exc_info.value.retryable is False
    assert "2056" in exc_info.value.message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"base_resp": {"status_code": 0}}),
        httpx.Response(
            200,
            json={
                "data": {"audio": "not-hex"},
                "base_resp": {"status_code": 0},
            },
        ),
    ],
)
async def test_minimax_rejects_malformed_success_response(
    response: httpx.Response,
) -> None:
    provider, http = _provider_with_transport(lambda request: response)
    try:
        with pytest.raises(TtsError) as exc_info:
            await provider.synthesize(SpeechRequest(text="你好"), _voice())
    finally:
        await http.aclose()
    assert exc_info.value.code == "tts_bad_response"


@pytest.mark.asyncio
async def test_minimax_timeout_is_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    provider, http = _provider_with_transport(handler)
    try:
        with pytest.raises(TtsError) as exc_info:
            await provider.synthesize(SpeechRequest(text="你好"), _voice())
    finally:
        await http.aclose()
    assert exc_info.value.code == "tts_timeout"
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_minimax_validates_request_before_network() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _success_response()

    cases = [
        (
            MiniMaxTtsBackendConfig(api_key=""),
            SpeechRequest(text="你好"),
            _voice(),
            "tts_bad_config",
        ),
        (
            MiniMaxTtsBackendConfig(api_key="key"),
            SpeechRequest(text="   "),
            _voice(),
            "tts_empty_text",
        ),
        (
            MiniMaxTtsBackendConfig(api_key="key"),
            SpeechRequest(text="你好", output_format="audio/ogg"),
            _voice(),
            "tts_bad_config",
        ),
    ]
    for backend, request, voice, expected_code in cases:
        provider, http = _provider_with_transport(handler, backend=backend)
        try:
            with pytest.raises(TtsError) as exc_info:
                await provider.synthesize(request, voice)
        finally:
            await http.aclose()
        assert exc_info.value.code == expected_code
    assert calls == 0


@pytest.mark.asyncio
async def test_speech_service_dispatches_to_minimax() -> None:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: _success_response())
    )
    config = parse_tts_config(
        {
            "default_backend": "minimax",
            "backends": {
                "minimax": {
                    "type": "minimax-t2a-v2",
                    "base_url": "https://minimax.example",
                    "api_key": "test-key",
                }
            },
            "voices": {
                "nahida": {
                    "backend": "minimax",
                    "voice_id": "NahidaDesktopTest_12345678",
                }
            },
            "default_voice": "nahida",
        }
    )
    service = SpeechService(config, http_client=http)
    try:
        artifact = await service.synthesize("你好")
    finally:
        await service.close()
        await http.aclose()

    assert artifact.data == MP3_BYTES
    assert artifact.mime_type == "audio/mpeg"
    assert artifact.provider == "minimax-t2a-v2"
    assert artifact.voice == "nahida"
    assert artifact.duration_ms == 4980
    assert "minimax-t2a-v2" in service.supported_provider_types()
