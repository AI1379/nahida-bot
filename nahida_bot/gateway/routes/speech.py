"""WebAPI speech endpoints: synthesis + cached media download.

Exposes the unified ``SpeechService`` over HTTP so the Desktop can request
high-quality TTS (GPT-SoVITS or future providers) and stream back the cached
audio without re-synthesizing on each replay. See
``docs/design/desktop-app.md`` §9.3.1 (Part B).

Routes:
- ``POST /api/speech/jobs`` — synchronously synthesize text, return a
  ``SpeechArtifactRef`` (artifact_id + metadata; the body bytes are cached
  server-side under ``artifact_id``).
- ``GET /api/media/speech/{artifact_id}`` — streaming file response for one
  cached artifact. 404 when missing/expired/evicted.

Both routes reuse the WebUI admin auth dependency (``require_token``), so the
Desktop pairs with an admin bearer and reuses it here.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from nahida_bot.gateway.deps import get_application
from nahida_bot.speech.base import SpeechArtifact, TtsError

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/speech", tags=["speech"])
media_router = APIRouter(prefix="/api/media", tags=["speech"])


class SpeechJobRequest(BaseModel):
    """Body of ``POST /api/speech/jobs``.

    Mirrors the unified ``SpeechRequest`` plus the voice/style knobs the
    Desktop DisplayPlan carries per segment.
    """

    text: str = Field(..., min_length=1, description="Text to synthesize.")
    voice: str = Field(
        default="",
        description="Voice name (key into webapi.speech.voices). Empty = default.",
    )
    text_lang: str = Field(default="", description="Optional language override.")
    style: str = Field(
        default="",
        description="Optional style/emotion hint; provider may ignore.",
    )
    speed: float = Field(
        default=0.0,
        description="Speed multiplier; 0.0 = provider default.",
    )
    pitch: float = Field(default=0.0, description="Pitch shift in semitones.")
    output_format: str = Field(
        default="",
        description="Optional MIME hint (e.g. audio/wav). Empty = provider default.",
    )


class SpeechJobResponse(BaseModel):
    """Response for a successful synthesis job.

    ``download_url`` is relative to the Gateway base URL; Desktop prefixes its
    configured HTTP origin at fetch time.
    """

    artifact_id: str
    download_url: str
    mime_type: str
    size_bytes: int
    duration_ms: int
    voice: str
    provider: str
    expires_at: str


def _get_services(request: Request) -> tuple[Any, Any, Any]:
    """Resolve speech services from app state, raising 503 if disabled."""
    service = getattr(request.app.state, "speech_service", None)
    store = getattr(request.app.state, "speech_artifact_store", None)
    config = getattr(request.app.state, "speech_config", None)
    if service is None or store is None or config is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "webapi.speech is not enabled; configure webapi.speech.backends "
                "and set webapi.speech.enabled: true"
            ),
        )
    return service, store, config


async def _synthesize_with_limits(
    service: Any,
    store: Any,
    config: Any,
    body: SpeechJobRequest,
) -> SpeechArtifact:
    """Run one synthesis call under the configured concurrency + length cap."""

    max_len = int(getattr(config, "max_text_length", 500) or 500)
    text = body.text.strip()
    if len(text) > max_len:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Speech text exceeds max_text_length ({max_len}).",
        )
    max_concurrency = max(1, int(getattr(config, "max_concurrency", 1) or 1))
    semaphore = _ConcurrencyScope.acquire(max_concurrency)
    try:
        async with semaphore:
            artifact = await service.synthesize(
                text,
                voice=body.voice,
                text_lang=body.text_lang,
                style=body.style,
                speed=body.speed,
                pitch=body.pitch,
                output_format=body.output_format,
            )
    except TtsError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": exc.code,
                "message": exc.message,
                "backend": exc.backend,
                "retryable": exc.retryable,
            },
        ) from exc
    return artifact


class _ConcurrencyScope:
    """Module-level semaphores keyed by max-concurrency value.

    The config value rarely changes within a process; caching one semaphore
    per value avoids creating a fresh one per request (which would defeat the
    cap) while staying correct if the operator bumps max_concurrency.
    """

    _cache: dict[int, asyncio.Semaphore] = {}

    @classmethod
    def acquire(cls, max_concurrency: int) -> asyncio.Semaphore:
        cached = cls._cache.get(max_concurrency)
        if cached is not None:
            return cached
        semaphore = asyncio.Semaphore(max_concurrency)
        cls._cache[max_concurrency] = semaphore
        return semaphore


@router.post("/jobs", response_model=SpeechJobResponse)
async def create_speech_job(
    body: SpeechJobRequest,
    request: Request,
    app=Depends(get_application),
) -> SpeechJobResponse:
    """Synchronously synthesize text and return a cached artifact reference."""
    service, store, config = _get_services(request)

    # TODO: resolve voice from actor/session persona context when available,
    # rather than always using the config-level default_voice (or the
    # explicitly passed voice name). Desktop currently passes voice="" so it
    # always hits default_voice. When persona-bound routing is implemented
    # the voice field should come from the credential's actor_account_key
    # or a session-level persona override.
    # Resolve the provider type from voice + config so the cache key matches
    # between find_cached and put (otherwise every call is a cache miss).
    provider_hint = service.resolve_provider_type(body.voice)

    # Cache hit → skip synthesis entirely (idempotent replay).
    cached = await store.find_cached(
        text=body.text,
        voice=body.voice,
        provider=provider_hint,
        style=body.style,
        speed=body.speed,
        pitch=body.pitch,
        output_format=body.output_format,
    )
    if cached is not None:
        public = cached.to_public_dict()
        return SpeechJobResponse(
            artifact_id=public["artifact_id"],
            download_url=f"/api/media/speech/{public['artifact_id']}",
            mime_type=public["mime_type"],
            size_bytes=public["size_bytes"],
            duration_ms=public["duration_ms"],
            voice=public["voice"],
            provider=public["provider"],
            expires_at=public["expires_at"],
        )

    artifact = await _synthesize_with_limits(service, store, config, body)
    stored = await store.put(
        text=body.text,
        voice=body.voice,
        provider=artifact.provider or provider_hint,
        style=body.style,
        speed=body.speed,
        pitch=body.pitch,
        output_format=body.output_format,
        artifact=artifact,
    )
    public = stored.to_public_dict()
    return SpeechJobResponse(
        artifact_id=public["artifact_id"],
        download_url=f"/api/media/speech/{public['artifact_id']}",
        mime_type=public["mime_type"],
        size_bytes=public["size_bytes"],
        duration_ms=public["duration_ms"],
        voice=public["voice"],
        provider=public["provider"],
        expires_at=public["expires_at"],
    )


@media_router.get("/speech/{artifact_id}")
async def download_speech_artifact(
    artifact_id: str,
    request: Request,
    app=Depends(get_application),
) -> FileResponse:
    """Stream one cached speech artifact by id."""
    _service, store, _config = _get_services(request)
    clean_id = artifact_id.strip()
    if not clean_id or "/" in clean_id or "\\" in clean_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid artifact id.",
        )
    stored = await store.get(clean_id)
    if stored is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Speech artifact is missing, expired or evicted.",
        )
    return FileResponse(
        path=str(stored.disk_path),
        media_type=stored.mime_type,
        filename=f"{clean_id}.{_ext_for_mime(stored.mime_type)}",
        headers={
            "Cache-Control": "no-store",
            "X-Artifact-Expires": stored.to_public_dict()["expires_at"],
        },
    )


def _ext_for_mime(mime_type: str) -> str:
    mapping = {
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "audio/wave": "wav",
        "audio/ogg": "ogg",
        "audio/aac": "aac",
        "audio/mpeg": "mp3",
        "audio/flac": "flac",
    }
    candidate = mapping.get((mime_type or "").lower())
    if candidate:
        return candidate
    import mimetypes

    guessed = mimetypes.guess_extension(mime_type or "")
    return guessed.lstrip(".") if guessed else "wav"


__all__ = ["router", "media_router"]
