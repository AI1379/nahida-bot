"""IndexTTS-2.5 adapter for the unified TTS service.

IndexTTS runs as a local HTTP microservice (see ``indextts_server.py`` in the
voice-changer project) because the model needs its own Python 3.11 environment
with torch, while the bot runs on 3.12. The adapter therefore speaks HTTP just
like the GPT-SoVITS one, which keeps the deployment decoupled: the engine can
live on another machine, restart independently of the bot, and be shared by
several consumers.

Service contract (``POST {base_url}{tts_path}``)::

    {"text": "...", "ref_audio": "D:\\\\...\\\\ref.wav", "lang": "ZH",
     "duration_factor": 1.0, "diffusion_steps": 20, "num_beams": 1,
     "peak_limit": 0.95}

Success returns ``audio/wav`` bytes with ``X-Audio-Duration-Ms`` /
``X-Infer-Ms`` / ``X-Audio-Peak`` metadata headers. Failures return
``{"error": {"code": "...", "message": "..."}}`` with a 4xx/5xx status.
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

#: ``duration_factor`` is the inverse of a speed multiplier: IndexTTS stretches
#: the waveform, so "twice as fast" means "half the duration".
_SPEED_TO_DURATION_EPSILON = 1e-6

#: Language aliases IndexTTS understands; everything else is upper-cased and
#: truncated to two letters so ``zh``/``ZH``/``zh-CN`` all reach the service as
#: ``ZH``.
_LANG_ALIASES = {
    "zh-cn": "ZH",
    "zh-tw": "ZH",
    "zh-hk": "ZH",
    "cmn": "ZH",
    "chinese": "ZH",
    "en-us": "EN",
    "en-gb": "EN",
    "english": "EN",
    "jp": "JA",
    "japanese": "JA",
    "spanish": "ES",
    "arabic": "AR",
}


def _normalize_lang(value: str, fallback: str = "ZH") -> str:
    raw = (value or "").strip()
    if not raw:
        return fallback
    alias = _LANG_ALIASES.get(raw.lower())
    if alias:
        return alias
    upper = raw.upper()
    return upper[:2] if len(upper) >= 2 else upper


class IndexTTSBackendConfig(BaseModel):
    """One IndexTTS service instance config."""

    model_config = ConfigDict(frozen=True, extra="allow")

    type: str = "indextts-v2"
    base_url: str = "http://127.0.0.1:9881"
    tts_path: str = "/tts"
    health_path: str = "/health"
    timeout_seconds: float = Field(default=300.0, ge=0.1)
    trust_env: bool = False
    force_close_connections: bool = True

    # Per-request synthesis defaults; each has a server-side equivalent.
    lang: str = "ZH"
    diffusion_steps: int = Field(default=20, ge=1, le=100)
    num_beams: int = Field(default=1, ge=1, le=8)
    duration_factor: float = Field(default=1.0, gt=0.0, le=4.0)
    peak_limit: float = Field(default=0.95, gt=0.0, le=1.0)
    extra_body: dict[str, Any] = Field(default_factory=dict)


class IndexTTSVoice(BaseModel):
    """IndexTTS zero-shot voice identity.

    ``ref_audio`` is a path **on the IndexTTS server** (the service stages the
    reference clip locally; there is no upload endpoint). ``emotions`` maps an
    emotion label to an alternative reference clip so a single voice can cover
    several delivery styles — the ``speak`` tool's ``emotion`` hint, and the
    ``SpeechRequest.style`` field, select through it.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    ref_audio: str = ""
    emotions: dict[str, str] = Field(default_factory=dict)
    lang: str = ""  # optional override; empty → request's text_lang
    diffusion_steps: int = Field(default=0, ge=0, le=100)
    num_beams: int = Field(default=0, ge=0, le=8)
    duration_factor: float = Field(default=0.0, ge=0.0, le=4.0)
    peak_limit: float = Field(default=0.0, ge=0.0, le=1.0)

    def resolved_ref_audio(self, style: str = "") -> str:
        """Pick the reference clip for ``style`` (falls back to ``ref_audio``)."""

        key = style.strip()
        if key and self.emotions:
            if key in self.emotions:
                return self.emotions[key]
            lowered = key.lower()
            for name, path in self.emotions.items():
                if name.strip().lower() == lowered:
                    return path
        return self.ref_audio

    def resolved_text_lang(self, fallback: str = "ZH") -> str:
        return self.lang.strip() or fallback


class IndexTTSProvider(TtsProvider):
    """TtsProvider adapter for a local IndexTTS-2.5 HTTP service."""

    type: ClassVar[str] = "indextts-v2"

    def __init__(
        self,
        backend_config: Any,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        # ``backend_config`` is typed ``Any`` at the seam so that
        # ``type[IndexTTSProvider]`` stays assignable to ``type[TtsProvider]``
        # (constructor contravariance). parse_backend_config guarantees the
        # concrete type; cast it back here.
        self._config = cast(IndexTTSBackendConfig, backend_config)
        self._client = http_client
        self._owns_client = http_client is None

    @staticmethod
    def parse_backend_config(raw: dict[str, Any]) -> IndexTTSBackendConfig:
        return IndexTTSBackendConfig(**raw)

    @staticmethod
    def parse_voice_config(raw: dict[str, Any]) -> IndexTTSVoice:
        return IndexTTSVoice(**raw)

    async def synthesize(
        self,
        request: SpeechRequest,
        voice_config: IndexTTSVoice,
    ) -> SpeechArtifact:
        clean_text = request.text.strip()
        if not clean_text:
            raise TtsError(
                "tts_empty_text",
                "Speech text is empty.",
                provider=self.type,
            )

        ref_audio = voice_config.resolved_ref_audio(request.style).strip()
        if not ref_audio:
            raise TtsError(
                "tts_missing_ref_audio",
                (
                    "Reference audio is not configured for this IndexTTS voice"
                    + (f" (style={request.style!r})" if request.style else "")
                    + "."
                ),
                provider=self.type,
            )

        payload = self._build_payload(clean_text, voice=voice_config, request=request)
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
                (
                    "IndexTTS synthesis timed out after "
                    f"{self._config.timeout_seconds:.0f}s."
                ),
                retryable=True,
                provider=self.type,
            ) from exc
        except httpx.HTTPError as exc:
            raise TtsError(
                "tts_transport_error",
                f"IndexTTS synthesis request failed: {exc}",
                retryable=True,
                provider=self.type,
            ) from exc

        return self._parse_response(response)

    async def health(self) -> dict[str, Any]:
        """Probe the service's ``/health`` endpoint (best effort)."""

        endpoint = (
            f"{self._config.base_url.rstrip('/')}/"
            f"{self._config.health_path.strip().lstrip('/')}"
        )
        try:
            response = await self._ensure_client().get(
                endpoint, timeout=min(self._config.timeout_seconds, 15.0)
            )
        except httpx.HTTPError as exc:
            return {"ok": False, "error": str(exc)}
        try:
            payload = response.json()
        except ValueError:
            return {"ok": False, "error": f"invalid JSON (HTTP {response.status_code})"}
        if not isinstance(payload, dict):
            return {"ok": False, "error": "unexpected body"}
        payload.setdefault("status_code", response.status_code)
        return payload

    async def close(self) -> None:
        if (
            self._client is not None
            and not self._client.is_closed
            and self._owns_client
        ):
            await self._client.aclose()
        self._client = None

    # ── internals ──────────────────────────────────────────────────────
    def _build_payload(
        self,
        text: str,
        *,
        voice: IndexTTSVoice,
        request: SpeechRequest,
    ) -> dict[str, Any]:
        config = self._config
        # extra_body provides defaults for unmodeled params; explicit fields
        # below override it (matches the GPT-SoVITS adapter's precedence).
        payload: dict[str, Any] = dict(config.extra_body)

        duration_factor = (
            voice.duration_factor
            or config.duration_factor
        )
        if request.speed and request.speed > _SPEED_TO_DURATION_EPSILON:
            duration_factor = 1.0 / request.speed

        payload.update(
            {
                "text": text,
                "ref_audio": voice.resolved_ref_audio(request.style).strip(),
                "lang": _normalize_lang(
                    voice.resolved_text_lang(request.text_lang or config.lang)
                ),
                "duration_factor": duration_factor,
                "diffusion_steps": voice.diffusion_steps or config.diffusion_steps,
                "num_beams": voice.num_beams or config.num_beams,
                "peak_limit": voice.peak_limit or config.peak_limit,
            }
        )
        return payload

    def _parse_response(self, response: httpx.Response) -> SpeechArtifact:
        status = response.status_code
        if status == 200:
            content_type = (
                response.headers.get("content-type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
            )
            if content_type.startswith("application/json"):
                raise TtsError(
                    "tts_bad_response",
                    "IndexTTS returned JSON with HTTP 200; "
                    f"body: {_truncate(response.text)}",
                    provider=self.type,
                )
            data = response.content
            if not data:
                raise TtsError(
                    "tts_bad_response",
                    "IndexTTS returned an empty audio body.",
                    provider=self.type,
                )
            return SpeechArtifact(
                data=data,
                mime_type=content_type or "audio/wav",
                duration_ms=_header_int(response, "X-Audio-Duration-Ms"),
                provider=self.type,
            )

        code, message = _error_code_message(response)

        if status == 400:
            raise TtsError(
                code or "tts_synthesis_failed",
                f"IndexTTS rejected the request: {message}",
                retryable=False,
                provider=self.type,
            )

        if status == 404:
            raise TtsError(
                code or "tts_missing_ref_audio",
                f"IndexTTS could not find the requested voice: {message}",
                retryable=False,
                provider=self.type,
            )

        if status == 503:
            # Model still warming up (load takes ~30-40 s on CPU).
            raise TtsError(
                "tts_server_error",
                f"IndexTTS service is not ready yet: {message}",
                retryable=True,
                provider=self.type,
            )

        if status == 429 or status >= 500:
            raise TtsError(
                "tts_server_error" if status >= 500 else "tts_rate_limited",
                f"IndexTTS returned HTTP {status}: {message}",
                retryable=True,
                provider=self.type,
            )

        raise TtsError(
            "tts_bad_response",
            f"IndexTTS returned unexpected HTTP {status}: {_truncate(response.text)}",
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


def _header_int(response: httpx.Response, name: str) -> int:
    raw = response.headers.get(name, "").strip()
    if not raw:
        return 0
    try:
        return max(0, int(float(raw)))
    except ValueError:
        return 0


def _error_code_message(response: httpx.Response) -> tuple[str, str]:
    """Extract ``(code, message)`` from an IndexTTS error body."""

    try:
        body = response.json()
    except ValueError:
        return "", _truncate(response.text)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            message = error.get("message")
            return (
                str(code).strip() if isinstance(code, str) else "",
                _truncate(message) if isinstance(message, str) else _truncate(str(body)),
            )
        for key in ("message", "detail"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return "", _truncate(value)
    return "", _truncate(str(body))


def _truncate(value: str, limit: int = 400) -> str:
    return value if len(value) <= limit else value[:limit] + "…"
