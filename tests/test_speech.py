"""Tests for the unified speech service: config, GPT-SoVITS provider, dispatch."""

from __future__ import annotations

import json

import httpx
import pytest

from nahida_bot.speech.base import SpeechRequest, TtsError
from nahida_bot.speech.config import TtsConfig, parse_tts_config
from nahida_bot.speech.providers.gpt_sovits import (
    GPTSoVITSBackendConfig,
    GPTSoVITSProvider,
    GPTSoVITSVoice,
)
from nahida_bot.speech.service import SpeechService

WAV_BYTES = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80\xbb\x00\x00\x00\x77\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"


def _gpt_sovits_backend_raw(**overrides: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "type": "gpt-sovits-v2",
        "base_url": "http://sovits.example:9880",
    }
    raw.update(overrides)
    return raw


def _provider_with_transport(
    handler,
    *,
    backend: GPTSoVITSBackendConfig | None = None,
) -> tuple[GPTSoVITSProvider, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GPTSoVITSProvider(
        backend or GPTSoVITSBackendConfig(base_url="http://sovits.example:9880"),
        http_client=http,
    ), http


def _voice(**overrides: object) -> GPTSoVITSVoice:
    base: dict[str, object] = {
        "ref_audio_path": "/data/nahida.wav",
        "prompt_text": "你好",
        "prompt_lang": "zh",
    }
    base.update(overrides)
    return GPTSoVITSVoice(**base)


# ── config ──────────────────────────────────────────────────────────────


def test_config_parses_backends_and_voices_as_raw_dicts() -> None:
    config = parse_tts_config(
        {
            "backends": {
                "default": {"type": "gpt-sovits-v2", "base_url": "http://x:9880"}
            },
            "voices": {
                "nahida": {"ref_audio_path": "/n.wav", "backend": "default"},
                "soft": {"ref_audio_path": "/s.wav"},
            },
            "default_voice": "nahida",
        }
    )
    # backends/voices stay raw — providers parse their own sub-configs.
    assert config.backends["default"]["type"] == "gpt-sovits-v2"
    assert config.voices["nahida"]["ref_audio_path"] == "/n.wav"


def test_config_resolve_voice_uses_explicit_then_default_then_only() -> None:
    config = parse_tts_config(
        {
            "voices": {
                "a": {"ref_audio_path": "/a.wav", "backend": "default"},
                "b": {"ref_audio_path": "/b.wav"},
            },
            "default_voice": "a",
            "backends": {"default": {"type": "gpt-sovits-v2"}},
        }
    )
    name, backend, raw = config.resolve_voice("b")
    assert name == "b"
    # b has no explicit backend → falls back to default_backend
    assert backend == "default"
    assert raw["ref_audio_path"] == "/b.wav"

    name, _, _ = config.resolve_voice("unknown")
    assert name == "a"  # default_voice fallback

    single = parse_tts_config({"voices": {"only": {"ref_audio_path": "/o.wav"}}})
    assert single.resolve_voice()[0] == "only"


def test_config_resolve_voice_raises_when_empty() -> None:
    config = parse_tts_config({})
    with pytest.raises(ValueError, match="not configured"):
        config.resolve_voice()


def test_config_backend_raw_requires_type_discriminator() -> None:
    config = parse_tts_config({"backends": {"nodefault": {"base_url": "http://x"}}})
    with pytest.raises(ValueError, match="type"):
        config.backend_raw("nodefault")


# ── GPT-SoVITS provider: success ────────────────────────────────────────


@pytest.mark.asyncio
async def test_provider_synthesize_success_returns_artifact() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, content=WAV_BYTES, headers={"content-type": "audio/wav"}
        )

    provider, http = _provider_with_transport(handler)
    try:
        artifact = await provider.synthesize(
            SpeechRequest(text="你好呀", text_lang="zh"),
            _voice(),
        )
    finally:
        await http.aclose()

    assert artifact.data == WAV_BYTES
    assert artifact.mime_type == "audio/wav"
    assert artifact.provider == "gpt-sovits-v2"

    assert captured["url"] == "http://sovits.example:9880/tts"
    body = captured["body"]
    assert body["text"] == "你好呀"
    assert body["ref_audio_path"] == "/data/nahida.wav"
    assert body["prompt_lang"] == "zh"
    assert body["text_lang"] == "zh"
    assert body["media_type"] == "wav"
    assert body["streaming_mode"] is False
    assert body["text_lang"] == body["text_lang"].lower()  # api_v2 wants lowercase


@pytest.mark.asyncio
async def test_provider_voice_text_lang_overrides_request() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, content=WAV_BYTES, headers={"content-type": "audio/wav"}
        )

    provider, http = _provider_with_transport(handler)
    try:
        # voice pins text_lang=ja; request.text_lang="zh" is ignored
        await provider.synthesize(
            SpeechRequest(text="hi", text_lang="zh"),
            _voice(text_lang="ja"),
        )
    finally:
        await http.aclose()

    assert captured["body"]["text_lang"] == "ja"


@pytest.mark.asyncio
async def test_provider_request_speed_overrides_backend_default() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, content=WAV_BYTES, headers={"content-type": "audio/wav"}
        )

    provider, http = _provider_with_transport(handler)
    try:
        await provider.synthesize(SpeechRequest(text="hi", speed=1.5), _voice())
    finally:
        await http.aclose()

    assert captured["body"]["speed_factor"] == 1.5


# ── GPT-SoVITS provider: error paths ────────────────────────────────────


@pytest.mark.asyncio
async def test_provider_400_extracts_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "ref_audio_path is required"})

    provider, http = _provider_with_transport(handler)
    try:
        with pytest.raises(TtsError) as exc_info:
            await provider.synthesize(SpeechRequest(text="hi"), _voice())
    finally:
        await http.aclose()

    assert exc_info.value.code == "tts_synthesis_failed"
    assert "ref_audio_path is required" in str(exc_info.value)
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_provider_500_retryable() -> None:
    provider, http = _provider_with_transport(
        lambda r: httpx.Response(500, json={"message": "boom"})
    )
    try:
        with pytest.raises(TtsError) as exc_info:
            await provider.synthesize(SpeechRequest(text="hi"), _voice())
    finally:
        await http.aclose()
    assert exc_info.value.code == "tts_server_error"
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_provider_timeout_retryable() -> None:
    provider, http = _provider_with_transport(
        lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("t"))
    )
    try:
        with pytest.raises(TtsError) as exc_info:
            await provider.synthesize(SpeechRequest(text="hi"), _voice())
    finally:
        await http.aclose()
    assert exc_info.value.code == "tts_timeout"
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_provider_rejects_json_on_200() -> None:
    provider, http = _provider_with_transport(
        lambda r: httpx.Response(200, json={"unexpected": True})
    )
    try:
        with pytest.raises(TtsError) as exc_info:
            await provider.synthesize(SpeechRequest(text="hi"), _voice())
    finally:
        await http.aclose()
    assert exc_info.value.code == "tts_bad_response"


@pytest.mark.asyncio
async def test_provider_rejects_empty_body() -> None:
    provider, http = _provider_with_transport(
        lambda r: httpx.Response(
            200, content=b"", headers={"content-type": "audio/wav"}
        )
    )
    try:
        with pytest.raises(TtsError) as exc_info:
            await provider.synthesize(SpeechRequest(text="hi"), _voice())
    finally:
        await http.aclose()
    assert exc_info.value.code == "tts_bad_response"


# ── GPT-SoVITS provider: client-side validation ────────────────────────


@pytest.mark.asyncio
async def test_provider_empty_text_no_request() -> None:
    provider, http = _provider_with_transport(
        lambda r: httpx.Response(200, content=b"x")
    )
    try:
        with pytest.raises(TtsError) as exc_info:
            await provider.synthesize(SpeechRequest(text="   "), _voice())
    finally:
        await http.aclose()
    assert exc_info.value.code == "tts_empty_text"


@pytest.mark.asyncio
async def test_provider_missing_ref_audio_no_request() -> None:
    provider, http = _provider_with_transport(
        lambda r: httpx.Response(200, content=b"x")
    )
    try:
        with pytest.raises(TtsError) as exc_info:
            await provider.synthesize(
                SpeechRequest(text="hi"), _voice(ref_audio_path="")
            )
    finally:
        await http.aclose()
    assert exc_info.value.code == "tts_missing_ref_audio"


@pytest.mark.asyncio
async def test_provider_429_retryable() -> None:
    provider, http = _provider_with_transport(
        lambda r: httpx.Response(429, json={"message": "slow down"})
    )
    try:
        with pytest.raises(TtsError) as exc_info:
            await provider.synthesize(SpeechRequest(text="hi"), _voice())
    finally:
        await http.aclose()
    assert exc_info.value.code == "tts_rate_limited"
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_provider_bad_media_type_raises_early() -> None:
    provider, http = _provider_with_transport(
        lambda r: httpx.Response(200, content=b"x"),
        backend=GPTSoVITSBackendConfig(media_type="mp3"),
    )
    try:
        with pytest.raises(TtsError) as exc_info:
            await provider.synthesize(SpeechRequest(text="hi"), _voice())
    finally:
        await http.aclose()
    assert exc_info.value.code == "tts_bad_config"


@pytest.mark.asyncio
async def test_provider_speed_unset_falls_back_to_backend_default() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, content=WAV_BYTES, headers={"content-type": "audio/wav"}
        )

    # backend pins speed_factor=0.8; request leaves speed unset (0.0)
    provider, http = _provider_with_transport(
        handler, backend=GPTSoVITSBackendConfig(speed_factor=0.8)
    )
    try:
        await provider.synthesize(SpeechRequest(text="hi"), _voice())
    finally:
        await http.aclose()
    assert captured["body"]["speed_factor"] == 0.8


# ── SpeechService dispatch ──────────────────────────────────────────────


def _service_config() -> TtsConfig:
    return parse_tts_config(
        {
            "default_backend": "default",
            "backends": {"default": _gpt_sovits_backend_raw()},
            "voices": {
                "nahida": {
                    "ref_audio_path": "/data/nahida.wav",
                    "prompt_text": "你好",
                    "prompt_lang": "zh",
                }
            },
            "default_voice": "nahida",
        }
    )


@pytest.mark.asyncio
async def test_service_dispatches_to_gpt_sovits_and_attributes_voice() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, content=WAV_BYTES, headers={"content-type": "audio/wav"}
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = SpeechService(_service_config(), http_client=http)
    try:
        artifact = await service.synthesize("你好呀")
    finally:
        await service.close()
        await http.aclose()

    assert artifact.data == WAV_BYTES
    assert artifact.voice == "nahida"
    assert artifact.provider == "gpt-sovits-v2"
    assert captured["body"]["ref_audio_path"] == "/data/nahida.wav"


@pytest.mark.asyncio
async def test_service_unknown_provider_type_raises() -> None:
    config = parse_tts_config(
        {
            "backends": {"default": {"type": "unknown-engine"}},
            "voices": {"v": {"ref_audio_path": "/x"}},
            "default_voice": "v",
        }
    )
    service = SpeechService(config)
    try:
        with pytest.raises(TtsError) as exc_info:
            await service.synthesize("hi")
    finally:
        await service.close()
    assert exc_info.value.code == "tts_unsupported_provider"
    assert exc_info.value.backend == "default"


@pytest.mark.asyncio
async def test_service_unknown_voice_raises_value_error() -> None:
    # Multiple voices, no default → an unknown name cannot be resolved.
    config = parse_tts_config(
        {
            "backends": {"default": {"type": "gpt-sovits-v2"}},
            "voices": {
                "a": {"ref_audio_path": "/a.wav"},
                "b": {"ref_audio_path": "/b.wav"},
            },
        }
    )
    service = SpeechService(config)
    try:
        with pytest.raises(ValueError, match="not configured"):
            await service.synthesize("hi", voice="ghost")
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_service_attaches_backend_on_provider_error() -> None:
    service = SpeechService(
        _service_config(),
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(500, json={"message": "boom"})
            )
        ),
    )
    try:
        with pytest.raises(TtsError) as exc_info:
            await service.synthesize("hi")
    finally:
        await service.close()
    # provider didn't set backend; service fills it from the resolved backend name.
    assert exc_info.value.backend == "default"
    assert exc_info.value.code == "tts_server_error"
