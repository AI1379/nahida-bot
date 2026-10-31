"""Tests for the IndexTTS-2.5 HTTP adapter and its registry wiring."""

from __future__ import annotations

import httpx
import pytest

from nahida_bot.speech.base import SpeechRequest, TtsError
from nahida_bot.speech.config import parse_tts_config
from nahida_bot.speech.providers.indextts import (
    IndexTTSBackendConfig,
    IndexTTSProvider,
    IndexTTSVoice,
)
from nahida_bot.speech.service import SpeechService

WAV_BYTES = (
    b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
    b"\x80\xbb\x00\x00\x00\x77\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
)
REF = r"D:\vc\models\gsv\v2ProPlus\三月七\reference_audios\中文\emotions\中立.wav"
HAPPY = r"D:\vc\models\gsv\v2ProPlus\三月七\reference_audios\中文\emotions\开心.wav"


def _backend(**overrides: object) -> IndexTTSBackendConfig:
    raw: dict[str, object] = {"base_url": "http://indextts.example:9881"}
    raw.update(overrides)
    return IndexTTSBackendConfig(**raw)


def _provider(handler, **overrides: object) -> tuple[IndexTTSProvider, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return IndexTTSProvider(_backend(**overrides), http_client=http), http


def _voice(**overrides: object) -> IndexTTSVoice:
    base: dict[str, object] = {"ref_audio": REF}
    base.update(overrides)
    return IndexTTSVoice(**base)


def _ok_handler(*, captured: list[httpx.Request] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        return httpx.Response(
            200,
            content=WAV_BYTES,
            headers={
                "content-type": "audio/wav",
                "X-Audio-Duration-Ms": "4145",
                "X-Infer-Ms": "38000",
            },
        )

    return handler


# ── config parsing ─────────────────────────────────────────────────────
def test_backend_config_defaults() -> None:
    config = IndexTTSBackendConfig()
    assert config.type == "indextts-v2"
    assert config.base_url == "http://127.0.0.1:9881"
    assert config.tts_path == "/tts"
    assert config.health_path == "/health"
    assert config.lang == "ZH"
    assert config.diffusion_steps == 20
    assert config.num_beams == 1
    assert config.peak_limit == pytest.approx(0.95)
    assert config.timeout_seconds == pytest.approx(300.0)


def test_voice_config_defaults_and_lang_fallback() -> None:
    voice = _voice()
    assert voice.emotions == {}
    assert voice.resolved_text_lang("ZH") == "ZH"
    assert _voice(lang="EN").resolved_text_lang("ZH") == "EN"


def test_voice_resolves_emotion_reference_audio() -> None:
    voice = _voice(emotions={"开心": HAPPY})
    assert voice.resolved_ref_audio("开心") == HAPPY
    assert voice.resolved_ref_audio("") == REF
    # 未知情绪回落到默认参考音频
    assert voice.resolved_ref_audio("生气") == REF


def test_voice_emotion_lookup_is_case_insensitive() -> None:
    voice = _voice(emotions={"Happy": HAPPY})
    assert voice.resolved_ref_audio("happy") == HAPPY
    assert voice.resolved_ref_audio(" HAPPY ") == HAPPY


# ── registry ───────────────────────────────────────────────────────────
def test_provider_is_registered_as_builtin() -> None:
    service = SpeechService(parse_tts_config({"backends": {}, "voices": {}}))
    assert "indextts-v2" in service.supported_provider_types()


def test_resolve_provider_type_for_indextts_voice() -> None:
    service = SpeechService(
        parse_tts_config(
            {
                "default_backend": "local",
                "backends": {"local": {"type": "indextts-v2"}},
                "voices": {"三月七": {"ref_audio": REF}},
            }
        )
    )
    assert service.resolve_provider_type("三月七") == "indextts-v2"


# ── synthesize: happy path ─────────────────────────────────────────────
async def test_synthesize_returns_artifact_with_duration() -> None:
    provider, http = _provider(_ok_handler())
    try:
        artifact = await provider.synthesize(SpeechRequest(text="你好"), _voice())
    finally:
        await http.aclose()
    assert artifact.data == WAV_BYTES
    assert artifact.mime_type == "audio/wav"
    assert artifact.duration_ms == 4145
    assert artifact.provider == "indextts-v2"


async def test_synthesize_payload_defaults() -> None:
    captured: list[httpx.Request] = []
    provider, http = _provider(_ok_handler(captured=captured))
    try:
        await provider.synthesize(SpeechRequest(text="  你好  "), _voice())
    finally:
        await http.aclose()
    assert len(captured) == 1
    request = captured[0]
    assert request.method == "POST"
    assert str(request.url) == "http://indextts.example:9881/tts"
    import json

    body = json.loads(request.content)
    assert body["text"] == "你好"
    assert body["ref_audio"] == REF
    assert body["lang"] == "ZH"
    assert body["diffusion_steps"] == 20
    assert body["num_beams"] == 1
    assert body["duration_factor"] == pytest.approx(1.0)
    assert body["peak_limit"] == pytest.approx(0.95)


async def test_synthesize_style_selects_emotion_reference() -> None:
    captured: list[httpx.Request] = []
    provider, http = _provider(_ok_handler(captured=captured))
    try:
        await provider.synthesize(
            SpeechRequest(text="你好", style="开心"),
            _voice(emotions={"开心": HAPPY}),
        )
    finally:
        await http.aclose()
    import json

    assert json.loads(captured[0].content)["ref_audio"] == HAPPY


async def test_synthesize_speed_inverts_duration_factor() -> None:
    captured: list[httpx.Request] = []
    provider, http = _provider(_ok_handler(captured=captured))
    try:
        await provider.synthesize(
            SpeechRequest(text="你好", speed=2.0), _voice()
        )
    finally:
        await http.aclose()
    import json

    # speed 2.0（两倍速）→ duration_factor 0.5
    assert json.loads(captured[0].content)["duration_factor"] == pytest.approx(0.5)


async def test_voice_overrides_backend_defaults() -> None:
    captured: list[httpx.Request] = []
    provider, http = _provider(
        _ok_handler(captured=captured), diffusion_steps=20, num_beams=1
    )
    voice = _voice(diffusion_steps=30, num_beams=3, lang="EN", peak_limit=0.8)
    try:
        await provider.synthesize(SpeechRequest(text="hi"), voice)
    finally:
        await http.aclose()
    import json

    body = json.loads(captured[0].content)
    assert body["diffusion_steps"] == 30
    assert body["num_beams"] == 3
    assert body["lang"] == "EN"
    assert body["peak_limit"] == pytest.approx(0.8)


async def test_voice_lang_wins_over_request_lang() -> None:
    """Voice-bound language overrides the per-request hint.

    Matches the GPT-SoVITS adapter's documented precedence: a voice pins its
    language, the request only supplies a fallback when the voice does not.
    """

    captured: list[httpx.Request] = []
    provider, http = _provider(_ok_handler(captured=captured))
    try:
        await provider.synthesize(
            SpeechRequest(text="hi", text_lang="JA"), _voice(lang="EN")
        )
    finally:
        await http.aclose()
    import json

    assert json.loads(captured[0].content)["lang"] == "EN"


async def test_request_lang_used_when_voice_does_not_pin_one() -> None:
    captured: list[httpx.Request] = []
    provider, http = _provider(_ok_handler(captured=captured))
    try:
        await provider.synthesize(SpeechRequest(text="hi", text_lang="ja"), _voice())
    finally:
        await http.aclose()
    import json

    # 大小写与别名统一到 IndexTTS 的两位大写码
    assert json.loads(captured[0].content)["lang"] == "JA"


async def test_extra_body_is_overridden_by_explicit_fields() -> None:
    captured: list[httpx.Request] = []
    provider, http = _provider(
        _ok_handler(captured=captured), extra_body={"num_beams": 9, "custom": "x"}
    )
    try:
        await provider.synthesize(SpeechRequest(text="hi"), _voice())
    finally:
        await http.aclose()
    import json

    body = json.loads(captured[0].content)
    assert body["num_beams"] == 1  # 显式字段覆盖 extra_body
    assert body["custom"] == "x"  # 未建模字段透传


# ── synthesize: failures ───────────────────────────────────────────────
async def test_empty_text_raises_before_any_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("不应发起请求")

    provider, http = _provider(handler)
    try:
        with pytest.raises(TtsError) as info:
            await provider.synthesize(SpeechRequest(text="   "), _voice())
    finally:
        await http.aclose()
    assert info.value.code == "tts_empty_text"
    assert info.value.retryable is False


async def test_missing_reference_audio_raises() -> None:
    provider, http = _provider(_ok_handler())
    try:
        with pytest.raises(TtsError) as info:
            await provider.synthesize(SpeechRequest(text="hi"), IndexTTSVoice())
    finally:
        await http.aclose()
    assert info.value.code == "tts_missing_ref_audio"


async def test_http_400_raises_non_retryable_synthesis_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"code": "empty_text", "message": "text 不能为空"}},
        )

    provider, http = _provider(handler)
    try:
        with pytest.raises(TtsError) as info:
            await provider.synthesize(SpeechRequest(text="hi"), _voice())
    finally:
        await http.aclose()
    assert info.value.code == "empty_text"
    assert info.value.retryable is False
    assert "text 不能为空" in info.value.message


async def test_http_404_raises_missing_reference() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404, json={"error": {"code": "voice_not_found", "message": "未知音色"}}
        )

    provider, http = _provider(handler)
    try:
        with pytest.raises(TtsError) as info:
            await provider.synthesize(SpeechRequest(text="hi"), _voice())
    finally:
        await http.aclose()
    assert info.value.code == "voice_not_found"
    assert info.value.retryable is False


async def test_http_503_means_model_still_loading() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503, json={"error": {"code": "model_not_ready", "message": "加载中"}}
        )

    provider, http = _provider(handler)
    try:
        with pytest.raises(TtsError) as info:
            await provider.synthesize(SpeechRequest(text="hi"), _voice())
    finally:
        await http.aclose()
    assert info.value.code == "tts_server_error"
    assert info.value.retryable is True


async def test_http_500_is_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={"error": {"code": "synthesis_failed", "message": "boom"}},
        )

    provider, http = _provider(handler)
    try:
        with pytest.raises(TtsError) as info:
            await provider.synthesize(SpeechRequest(text="hi"), _voice())
    finally:
        await http.aclose()
    assert info.value.code == "tts_server_error"
    assert info.value.retryable is True


async def test_timeout_is_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    provider, http = _provider(handler)
    try:
        with pytest.raises(TtsError) as info:
            await provider.synthesize(SpeechRequest(text="hi"), _voice())
    finally:
        await http.aclose()
    assert info.value.code == "tts_timeout"
    assert info.value.retryable is True


async def test_transport_error_is_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    provider, http = _provider(handler)
    try:
        with pytest.raises(TtsError) as info:
            await provider.synthesize(SpeechRequest(text="hi"), _voice())
    finally:
        await http.aclose()
    assert info.value.code == "tts_transport_error"
    assert info.value.retryable is True


async def test_json_body_with_200_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    provider, http = _provider(handler)
    try:
        with pytest.raises(TtsError) as info:
            await provider.synthesize(SpeechRequest(text="hi"), _voice())
    finally:
        await http.aclose()
    assert info.value.code == "tts_bad_response"


async def test_empty_audio_body_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"", headers={"content-type": "audio/wav"})

    provider, http = _provider(handler)
    try:
        with pytest.raises(TtsError) as info:
            await provider.synthesize(SpeechRequest(text="hi"), _voice())
    finally:
        await http.aclose()
    assert info.value.code == "tts_bad_response"


# ── health ─────────────────────────────────────────────────────────────
async def test_health_returns_service_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(
            200,
            json={"ok": True, "loaded": True, "version": "2.5", "sr": 22050},
        )

    provider, http = _provider(handler)
    try:
        payload = await provider.health()
    finally:
        await http.aclose()
    assert payload["loaded"] is True
    assert payload["version"] == "2.5"
    assert payload["status_code"] == 200


async def test_health_reports_transport_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    provider, http = _provider(handler)
    try:
        payload = await provider.health()
    finally:
        await http.aclose()
    assert payload["ok"] is False
    assert "refused" in payload["error"]


# ── service dispatch ───────────────────────────────────────────────────
async def test_service_dispatches_to_indextts() -> None:
    captured: list[httpx.Request] = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(_ok_handler(captured=captured)))
    service = SpeechService(
        parse_tts_config(
            {
                "default_backend": "local",
                "default_voice": "三月七",
                "backends": {
                    "local": {
                        "type": "indextts-v2",
                        "base_url": "http://indextts.example:9881",
                    }
                },
                "voices": {
                    "三月七": {
                        "ref_audio": REF,
                        "emotions": {"开心": HAPPY},
                    }
                },
            }
        ),
        http_client=http,
    )
    try:
        artifact = await service.synthesize("你好呀", style="开心")
    finally:
        await http.aclose()
    assert artifact.provider == "indextts-v2"
    assert artifact.voice == "三月七"
    assert artifact.duration_ms == 4145
    import json

    assert json.loads(captured[0].content)["ref_audio"] == HAPPY
