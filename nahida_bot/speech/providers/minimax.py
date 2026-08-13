"""MiniMax synchronous T2A adapter for the unified TTS service.

Implements the non-streaming ``POST /v1/t2a_v2`` contract. MiniMax returns
audio as a hex string inside a JSON envelope; this adapter decodes it into a
normal :class:`~nahida_bot.speech.base.SpeechArtifact` so callers do not need
to know anything about the vendor-specific response format.
"""

from __future__ import annotations

from typing import Any, ClassVar, cast

import httpx
from pydantic import BaseModel, ConfigDict, Field

from nahida_bot.speech.base import (
    SpeechArtifact,
    SpeechRequest,
    TtsError,
    TtsProvider,
)

_SUPPORTED_AUDIO_FORMATS = frozenset({"mp3", "pcm", "flac", "wav"})
_MIME_TYPES = {
    "mp3": "audio/mpeg",
    "pcm": "audio/pcm",
    "flac": "audio/flac",
    "wav": "audio/wav",
}
_MIME_TO_FORMAT = {
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/pcm": "pcm",
    "audio/l16": "pcm",
    "audio/flac": "flac",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
}
_LANGUAGE_BOOST_BY_CODE = {
    "zh": "Chinese",
    "zh-cn": "Chinese",
    "cmn": "Chinese",
    "yue": "Chinese,Yue",
    "zh-yue": "Chinese,Yue",
    "en": "English",
    "ja": "Japanese",
    "jp": "Japanese",
    "ko": "Korean",
    "kr": "Korean",
}
_SUPPORTED_EMOTIONS = frozenset(
    {
        "happy",
        "sad",
        "angry",
        "fearful",
        "disgusted",
        "surprised",
        "calm",
        "fluent",
        # MiniMax's API currently spells this value "whipser".
        "whipser",
    }
)
_API_ERROR_MAP: dict[int, tuple[str, bool]] = {
    1000: ("tts_server_error", True),
    1001: ("tts_timeout", True),
    1002: ("tts_rate_limited", True),
    1004: ("tts_auth_failed", False),
    1008: ("tts_quota_exceeded", False),
    1024: ("tts_server_error", True),
    1026: ("tts_synthesis_failed", False),
    1027: ("tts_synthesis_failed", False),
    1033: ("tts_server_error", True),
    1041: ("tts_rate_limited", True),
    2013: ("tts_synthesis_failed", False),
    2045: ("tts_rate_limited", True),
    2049: ("tts_auth_failed", False),
    2056: ("tts_quota_exceeded", False),
}


class MiniMaxTtsBackendConfig(BaseModel):
    """One MiniMax synchronous T2A backend instance."""

    model_config = ConfigDict(frozen=True, extra="allow")

    type: str = "minimax-t2a-v2"
    base_url: str = "https://api.minimaxi.com"
    tts_path: str = "/v1/t2a_v2"
    api_key: str = ""
    require_api_key: bool = True
    model: str = "speech-2.8-hd"
    timeout_seconds: float = Field(default=60.0, ge=0.1)
    trust_env: bool = False
    force_close_connections: bool = False

    audio_format: str = "mp3"
    sample_rate: int = 32000
    bitrate: int = 128000
    channel: int = 1
    language_boost: str = "auto"
    aigc_watermark: bool = False
    extra_body: dict[str, Any] = Field(default_factory=dict)


class MiniMaxTtsVoice(BaseModel):
    """A MiniMax system, cloned, or designed voice identity."""

    model_config = ConfigDict(frozen=True, extra="allow")

    voice_id: str
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    volume: float = Field(default=1.0, gt=0.0)
    pitch: int = Field(default=0, ge=-12, le=12)
    emotion: str = ""
    language_boost: str = ""


class MiniMaxTtsProvider(TtsProvider):
    """TTS provider for MiniMax's synchronous ``t2a_v2`` endpoint."""

    type: ClassVar[str] = "minimax-t2a-v2"

    def __init__(
        self,
        backend_config: Any,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = cast(MiniMaxTtsBackendConfig, backend_config)
        self._client = http_client
        self._owns_client = http_client is None

    @staticmethod
    def parse_backend_config(raw: dict[str, Any]) -> MiniMaxTtsBackendConfig:
        return MiniMaxTtsBackendConfig(**raw)

    @staticmethod
    def parse_voice_config(raw: dict[str, Any]) -> MiniMaxTtsVoice:
        return MiniMaxTtsVoice(**raw)

    async def synthesize(
        self,
        request: SpeechRequest,
        voice_config: MiniMaxTtsVoice,
    ) -> SpeechArtifact:
        text = request.text.strip()
        if not text:
            raise TtsError(
                "tts_empty_text",
                "Speech text is empty.",
                provider=self.type,
            )
        if len(text) >= 10_000:
            raise TtsError(
                "tts_text_too_long",
                "MiniMax synchronous TTS text must be shorter than 10,000 characters.",
                provider=self.type,
            )

        api_key = self._config.api_key.strip()
        if self._config.require_api_key and not api_key:
            raise TtsError(
                "tts_bad_config",
                "MiniMax TTS API key is not configured.",
                provider=self.type,
            )
        voice_id = voice_config.voice_id.strip()
        if not voice_id:
            raise TtsError(
                "tts_bad_config",
                "MiniMax voice_id is not configured for this voice.",
                provider=self.type,
            )

        audio_format = self._resolve_audio_format(request.output_format)
        self._validate_audio_settings(audio_format)
        payload = self._build_payload(
            text,
            request=request,
            voice=voice_config,
            audio_format=audio_format,
        )
        endpoint = (
            f"{self._config.base_url.rstrip('/')}/"
            f"{self._config.tts_path.strip().lstrip('/')}"
        )
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if self._config.force_close_connections:
            headers["Connection"] = "close"

        try:
            response = await self._ensure_client().post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=self._config.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise TtsError(
                "tts_timeout",
                "MiniMax synthesis request timed out.",
                retryable=True,
                provider=self.type,
            ) from exc
        except httpx.HTTPError as exc:
            raise TtsError(
                "tts_transport_error",
                f"MiniMax synthesis request failed: {exc}",
                retryable=True,
                provider=self.type,
            ) from exc

        return self._parse_response(response, requested_format=audio_format)

    async def close(self) -> None:
        if (
            self._client is not None
            and not self._client.is_closed
            and self._owns_client
        ):
            await self._client.aclose()
        self._client = None

    def _build_payload(
        self,
        text: str,
        *,
        request: SpeechRequest,
        voice: MiniMaxTtsVoice,
        audio_format: str,
    ) -> dict[str, Any]:
        config = self._config
        emotion = _resolve_emotion(request.style, voice.emotion)
        voice_setting: dict[str, Any] = {
            "voice_id": voice.voice_id.strip(),
            "speed": request.speed if request.speed else voice.speed,
            "vol": voice.volume,
            "pitch": _rounded_pitch(request.pitch) if request.pitch else voice.pitch,
        }
        if emotion:
            voice_setting["emotion"] = emotion

        payload: dict[str, Any] = dict(config.extra_body)
        payload.update(
            {
                "model": config.model.strip() or "speech-2.8-hd",
                "text": text,
                "stream": False,
                "voice_setting": voice_setting,
                "audio_setting": {
                    "sample_rate": config.sample_rate,
                    "bitrate": config.bitrate,
                    "format": audio_format,
                    "channel": config.channel,
                },
                "subtitle_enable": False,
                "output_format": "hex",
                "aigc_watermark": config.aigc_watermark,
            }
        )
        language_boost = self._resolve_language_boost(request, voice)
        if language_boost:
            payload["language_boost"] = language_boost
        return payload

    def _resolve_audio_format(self, requested: str) -> str:
        value = requested.strip().lower()
        if value:
            audio_format = _MIME_TO_FORMAT.get(value, value.removeprefix("audio/"))
        else:
            audio_format = self._config.audio_format.strip().lower() or "mp3"
        if audio_format not in _SUPPORTED_AUDIO_FORMATS:
            raise TtsError(
                "tts_bad_config",
                (
                    "MiniMax audio format must be one of "
                    f"{sorted(_SUPPORTED_AUDIO_FORMATS)}, got {audio_format!r}."
                ),
                provider=self.type,
            )
        return audio_format

    def _validate_audio_settings(self, audio_format: str) -> None:
        config = self._config
        if config.sample_rate not in {8000, 16000, 22050, 24000, 32000, 44100}:
            raise TtsError(
                "tts_bad_config",
                f"Unsupported MiniMax sample_rate: {config.sample_rate}.",
                provider=self.type,
            )
        if config.channel not in {1, 2}:
            raise TtsError(
                "tts_bad_config",
                f"MiniMax channel must be 1 or 2, got {config.channel}.",
                provider=self.type,
            )
        if audio_format == "mp3" and config.bitrate not in {
            32000,
            64000,
            128000,
            256000,
        }:
            raise TtsError(
                "tts_bad_config",
                f"Unsupported MiniMax MP3 bitrate: {config.bitrate}.",
                provider=self.type,
            )

    def _resolve_language_boost(
        self,
        request: SpeechRequest,
        voice: MiniMaxTtsVoice,
    ) -> str:
        if voice.language_boost.strip():
            return voice.language_boost.strip()
        language_code = request.text_lang.strip().lower()
        if language_code:
            mapped = _LANGUAGE_BOOST_BY_CODE.get(language_code)
            if mapped:
                return mapped
        return self._config.language_boost.strip()

    def _parse_response(
        self,
        response: httpx.Response,
        *,
        requested_format: str,
    ) -> SpeechArtifact:
        if response.status_code >= 400:
            body = _try_json_object(response)
            raise _http_error(response, body, provider=self.type)

        body = _json_object(response)
        _raise_for_base_resp(body, provider=self.type)
        data = body.get("data")
        if not isinstance(data, dict):
            raise TtsError(
                "tts_bad_response",
                "MiniMax response is missing the data object.",
                provider=self.type,
            )
        audio_hex = data.get("audio")
        if not isinstance(audio_hex, str) or not audio_hex.strip():
            raise TtsError(
                "tts_bad_response",
                "MiniMax response did not contain audio data.",
                provider=self.type,
            )
        try:
            audio = bytes.fromhex(audio_hex.strip())
        except ValueError as exc:
            raise TtsError(
                "tts_bad_response",
                "MiniMax returned invalid hex-encoded audio data.",
                provider=self.type,
            ) from exc
        if not audio:
            raise TtsError(
                "tts_bad_response",
                "MiniMax returned an empty audio payload.",
                provider=self.type,
            )

        extra_info = body.get("extra_info")
        response_format = requested_format
        duration_ms = 0
        if isinstance(extra_info, dict):
            candidate_format = str(extra_info.get("audio_format", "")).lower()
            if candidate_format in _SUPPORTED_AUDIO_FORMATS:
                response_format = candidate_format
            duration_ms = _non_negative_int(extra_info.get("audio_length"))
        return SpeechArtifact(
            data=audio,
            mime_type=_MIME_TYPES[response_format],
            duration_ms=duration_ms,
            provider=self.type,
        )

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                limits=httpx.Limits(
                    max_keepalive_connections=0
                    if self._config.force_close_connections
                    else None
                ),
                trust_env=self._config.trust_env,
            )
            self._owns_client = True
        return self._client


def _resolve_emotion(request_style: str, voice_emotion: str) -> str:
    style = request_style.strip().lower()
    if style == "whisper":
        style = "whipser"
    if style in _SUPPORTED_EMOTIONS:
        return style
    configured = voice_emotion.strip().lower()
    if configured == "whisper":
        configured = "whipser"
    return configured


def _rounded_pitch(value: float) -> int:
    return max(-12, min(12, round(value)))


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(float(str(value))))
    except (TypeError, ValueError):
        return 0


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise TtsError(
            "tts_bad_response",
            f"MiniMax returned non-JSON HTTP {response.status_code}.",
            provider=MiniMaxTtsProvider.type,
        ) from exc
    if not isinstance(body, dict):
        raise TtsError(
            "tts_bad_response",
            "MiniMax returned a non-object JSON response.",
            provider=MiniMaxTtsProvider.type,
        )
    return body


def _try_json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _http_error(
    response: httpx.Response,
    body: dict[str, Any],
    *,
    provider: str,
) -> TtsError:
    status = response.status_code
    api_code, message = _base_resp_details(body)
    if not message:
        message = _truncate(response.text)
    detail = f"MiniMax returned HTTP {status}"
    if api_code is not None:
        detail += f" (API {api_code})"
    if message:
        detail += f": {message}"
    if api_code in _API_ERROR_MAP:
        code, retryable = _API_ERROR_MAP[api_code]
        return TtsError(code, detail, retryable=retryable, provider=provider)
    if status in {401, 403}:
        return TtsError("tts_auth_failed", detail, provider=provider)
    if status == 429:
        return TtsError("tts_rate_limited", detail, retryable=True, provider=provider)
    if status >= 500:
        return TtsError("tts_server_error", detail, retryable=True, provider=provider)
    return TtsError("tts_synthesis_failed", detail, provider=provider)


def _raise_for_base_resp(body: dict[str, Any], *, provider: str) -> None:
    status_code, status_msg = _base_resp_details(body)
    if status_code is None or status_code == 0:
        return
    code, retryable = _API_ERROR_MAP.get(status_code, ("tts_synthesis_failed", False))
    raise TtsError(
        code,
        f"MiniMax API returned error {status_code}: {status_msg or 'unknown error'}",
        retryable=retryable,
        provider=provider,
    )


def _base_resp_details(body: dict[str, Any]) -> tuple[int | None, str]:
    base_resp = body.get("base_resp")
    if not isinstance(base_resp, dict):
        message = body.get("message")
        return None, str(message) if message is not None else ""
    raw_code = base_resp.get("status_code")
    try:
        code = int(raw_code) if raw_code is not None else None
    except (TypeError, ValueError):
        code = None
    return code, str(base_resp.get("status_msg", "")).strip()


def _truncate(value: str, limit: int = 400) -> str:
    clean = value.strip()
    return clean if len(clean) <= limit else clean[:limit] + "…"
