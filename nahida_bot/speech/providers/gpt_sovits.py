"""GPT-SoVITS api_v2 adapter for the unified TTS service.

Implements ``POST /tts`` (blocking) against the api_v2 contract verified from
``api_v2.py``. Success returns ``audio/{media_type}`` bytes (HTTP 200); failure
returns JSON ``{message, Exception}`` (HTTP 400).
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

_SUPPORTED_MEDIA_TYPES = frozenset({"wav", "raw", "ogg", "aac"})


class GPTSoVITSBackendConfig(BaseModel):
    """One GPT-SoVITS api_v2 backend instance config."""

    model_config = ConfigDict(frozen=True, extra="allow")

    type: str = "gpt-sovits-v2"
    base_url: str = "http://127.0.0.1:9880"
    tts_path: str = "/tts"
    timeout_seconds: float = Field(default=180.0, ge=0.1)
    trust_env: bool = False
    force_close_connections: bool = True

    # api_v2 per-request inference parameters (all have api_v2 defaults).
    media_type: str = "wav"
    text_split_method: str = "cut5"
    top_k: int = 15
    top_p: float = 1.0
    temperature: float = 1.0
    speed_factor: float = 1.0
    repetition_penalty: float = 1.35
    batch_size: int = 1
    sample_steps: int = 32
    super_sampling: bool = False
    parallel_infer: bool = True
    extra_body: dict[str, Any] = Field(default_factory=dict)


class GPTSoVITSVoice(BaseModel):
    """GPT-SoVITS zero-shot voice identity.

    ``ref_audio_path`` is a path on the api_v2 server (api_v2 has no upload
    endpoint); the deployer must stage the reference audio there.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    ref_audio_path: str
    prompt_text: str = ""
    prompt_lang: str = "zh"
    text_lang: str = ""  # optional override; empty → request's text_lang

    def resolved_text_lang(self, fallback: str = "zh") -> str:
        return self.text_lang.strip() or fallback


class GPTSoVITSProvider(TtsProvider):
    """TtsProvider adapter for GPT-SoVITS api_v2."""

    type: ClassVar[str] = "gpt-sovits-v2"

    def __init__(
        self,
        backend_config: Any,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        # ``backend_config`` is typed ``Any`` at the seam so that
        # ``type[GPTSoVITSProvider]`` is assignable to ``type[TtsProvider]``
        # (constructor contravariance). parse_backend_config guarantees the
        # concrete type; cast it back here.
        self._config = cast(GPTSoVITSBackendConfig, backend_config)
        self._client = http_client
        self._owns_client = http_client is None

    @staticmethod
    def parse_backend_config(raw: dict[str, Any]) -> GPTSoVITSBackendConfig:
        return GPTSoVITSBackendConfig(**raw)

    @staticmethod
    def parse_voice_config(raw: dict[str, Any]) -> GPTSoVITSVoice:
        return GPTSoVITSVoice(**raw)

    async def synthesize(
        self,
        request: SpeechRequest,
        voice_config: GPTSoVITSVoice,
    ) -> SpeechArtifact:
        clean_text = request.text.strip()
        if not clean_text:
            raise TtsError(
                "tts_empty_text",
                "Speech text is empty.",
                provider=self.type,
            )
        clean_ref = voice_config.ref_audio_path.strip()
        if not clean_ref:
            raise TtsError(
                "tts_missing_ref_audio",
                "Reference audio path is not configured for this voice.",
                provider=self.type,
            )

        media_type = (
            request.output_format.removeprefix("audio/").strip()
            or self._config.media_type.strip()
            or "wav"
        )
        if media_type not in _SUPPORTED_MEDIA_TYPES:
            raise TtsError(
                "tts_bad_config",
                (
                    "GPT-SoVITS media_type must be one of "
                    f"{sorted(_SUPPORTED_MEDIA_TYPES)}, got {media_type!r}."
                ),
                provider=self.type,
            )

        payload = self._build_payload(
            clean_text,
            voice=voice_config,
            request=request,
            media_type=media_type,
        )
        endpoint = (
            f"{self._config.base_url.rstrip('/')}/"
            f"{self._config.tts_path.strip().lstrip('/')}"
        )
        headers: dict[str, str] = {"Content-Type": "application/json"}
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
                "GPT-SoVITS synthesis request timed out.",
                retryable=True,
                provider=self.type,
            ) from exc
        except httpx.HTTPError as exc:
            raise TtsError(
                "tts_transport_error",
                f"GPT-SoVITS synthesis request failed: {exc}",
                retryable=True,
                provider=self.type,
            ) from exc

        return self._parse_response(response, media_type=media_type)

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
        voice: GPTSoVITSVoice,
        request: SpeechRequest,
        media_type: str,
    ) -> dict[str, Any]:
        config = self._config
        text_lang = voice.resolved_text_lang(request.text_lang or "zh")
        # extra_body provides defaults for unmodeled api_v2 params; explicit
        # fields below override it (matches image_generation precedence).
        payload: dict[str, Any] = dict(config.extra_body)
        payload.update(
            {
                "text": text,
                "text_lang": text_lang.strip().lower() or "zh",
                "ref_audio_path": voice.ref_audio_path,
                "prompt_text": voice.prompt_text,
                "prompt_lang": voice.prompt_lang.strip().lower() or "zh",
                "media_type": media_type,
                "text_split_method": config.text_split_method,
                "top_k": config.top_k,
                "top_p": config.top_p,
                "temperature": config.temperature,
                "speed_factor": request.speed if request.speed else config.speed_factor,
                "repetition_penalty": config.repetition_penalty,
                "batch_size": config.batch_size,
                "sample_steps": config.sample_steps,
                "super_sampling": config.super_sampling,
                "parallel_infer": config.parallel_infer,
                # Always blocking; GPT-SoVITS streaming is not used (see design §4).
                "streaming_mode": False,
            }
        )
        return payload

    def _parse_response(
        self,
        response: httpx.Response,
        *,
        media_type: str,
    ) -> SpeechArtifact:
        status = response.status_code
        if status == 200:
            content_type = (
                response.headers.get("content-type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
            )
            # Defensive: api_v2 should return audio on 200; reject JSON.
            if content_type.startswith("application/json"):
                raise TtsError(
                    "tts_bad_response",
                    "GPT-SoVITS returned JSON with HTTP 200; "
                    f"body: {_truncate(response.text)}",
                    provider=self.type,
                )
            mime = content_type or f"audio/{media_type}"
            data = response.content
            if not data:
                raise TtsError(
                    "tts_bad_response",
                    "GPT-SoVITS returned an empty audio body.",
                    provider=self.type,
                )
            return SpeechArtifact(data=data, mime_type=mime, provider=self.type)

        if status == 400:
            raise TtsError(
                "tts_synthesis_failed",
                f"GPT-SoVITS rejected the request: {_response_message(response)}",
                # 400 covers both param errors and inference exceptions;
                # conservatively non-retryable, let the caller degrade to text.
                retryable=False,
                provider=self.type,
            )

        if status == 429 or status >= 500:
            raise TtsError(
                "tts_server_error" if status >= 500 else "tts_rate_limited",
                f"GPT-SoVITS returned HTTP {status}: {_response_message(response)}",
                retryable=True,
                provider=self.type,
            )

        raise TtsError(
            "tts_bad_response",
            f"GPT-SoVITS returned unexpected HTTP {status}: {_truncate(response.text)}",
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


def _response_message(response: httpx.Response) -> str:
    """Best-effort extraction of the ``message`` field from an api_v2 error body."""

    try:
        body = response.json()
    except ValueError:
        return _truncate(response.text)
    if isinstance(body, dict):
        message = body.get("message")
        if isinstance(message, str) and message.strip():
            return _truncate(message)
        detail = body.get("Exception") or body.get("exception")
        if isinstance(detail, str) and detail.strip():
            return _truncate(detail)
    return _truncate(str(body))


def _truncate(value: str, limit: int = 400) -> str:
    return value if len(value) <= limit else value[:limit] + "…"
