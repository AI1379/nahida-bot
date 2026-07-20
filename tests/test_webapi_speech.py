"""End-to-end tests for the WebAPI speech pipeline (Desktop TTS Part B).

Mirrors the Desktop `GatewayAudioAdapter` flow:

    POST /api/speech/jobs   -> artifact_id + download_url
    GET  /api/media/speech/{id}  -> audio bytes

Both endpoints are admin-gated (``require_token``). The tests stand up a
``WebAPIApp`` with a stubbed TTS provider so no real GPT-SoVITS instance is
needed.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from nahida_bot.core.config import (
    Settings,
    WebAPIConfigModel,
    WebApiSpeechConfigModel,
    WebUIAuthConfigModel,
    WebUIConfigModel,
)
from nahida_bot.gateway.app import WebAPIApp
from nahida_bot.speech.base import SpeechArtifact, SpeechRequest, TtsProvider


WAV_BYTES = (
    b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
    b"\x80\xbb\x00\x00\x00\x77\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
)


class _StubTtsProvider(TtsProvider):
    """In-memory TtsProvider that returns deterministic bytes per request."""

    type: str = "stub-tts"  # type: ignore[assignment]

    calls: list[SpeechRequest] = []
    next_payload: bytes = WAV_BYTES
    fail_with: str = ""

    @staticmethod
    def parse_backend_config(raw: dict[str, Any]) -> dict[str, Any]:
        return raw

    @staticmethod
    def parse_voice_config(raw: dict[str, Any]) -> dict[str, Any]:
        return raw

    def __init__(self, backend_config: Any, *, http_client: Any = None) -> None:
        self._config = backend_config

    async def synthesize(
        self,
        request: SpeechRequest,
        voice_config: Any,
    ) -> SpeechArtifact:
        type(self).calls.append(request)
        if self.fail_with:
            from nahida_bot.speech.base import TtsError

            raise TtsError(
                "tts_stub_error",
                self.fail_with,
                provider=self.type,
            )
        return SpeechArtifact(
            data=type(self).next_payload,
            mime_type="audio/wav",
            duration_ms=120,
            provider=self.type,
            voice="stub",
        )

    async def close(self) -> None:
        return None


def _make_mock_app(
    *,
    auth_token: str = "admin-secret",
    speech_enabled: bool = True,
    cache_dir: str = "",
) -> MagicMock:
    settings = Settings(
        app_name="Test WebAPI",
        debug=True,
        db_path=":memory:",
        plugin_paths=[],
        discover_builtin_channels=False,
        webapi=WebAPIConfigModel(
            auth_token=auth_token,
            speech=WebApiSpeechConfigModel(
                enabled=speech_enabled,
                backends={"default": {"type": "stub-tts"}},
                voices={"default": {"ref_audio_path": "/stub.wav"}},
                default_voice="default",
                artifact_cache_dir=cache_dir or "./data/speech_cache_test",
            ),
        ),
        webui=WebUIConfigModel(auth=WebUIAuthConfigModel()),
    )
    mock = MagicMock(spec=["settings"])
    mock.settings = settings
    return mock


def _patch_speech_service(
    webapi_app: WebAPIApp,
    *,
    cache_dir: Path,
) -> None:
    """Replace the auto-built SpeechService with one using the stub provider."""
    assert webapi_app.speech_service is not None
    assert webapi_app.speech_artifact_store is not None
    # Register the stub adapter on the live service so synthesize() dispatches
    # to it instead of the real GPT-SoVITS client.
    webapi_app.speech_service.register_provider(_StubTtsProvider)
    # Move the artifact store to a per-test temp dir.
    from nahida_bot.speech.artifact_store import SpeechArtifactStore

    webapi_app.speech_artifact_store = SpeechArtifactStore(cache_dir)
    webapi_app._fastapi.state.speech_artifact_store = (  # type: ignore[attr-defined]
        webapi_app.speech_artifact_store
    )


@pytest.fixture
def webapi_with_speech(tmp_path: Path) -> WebAPIApp:
    cache_dir = str(tmp_path / "speech_cache")
    mock = _make_mock_app(cache_dir=cache_dir)
    app = WebAPIApp(
        application=mock,
        host="127.0.0.1",
        port=6185,
        auth_token="admin-secret",
    )
    _patch_speech_service(app, cache_dir=tmp_path / "speech_cache")
    return app


@pytest.fixture
async def speech_client(
    webapi_with_speech: WebAPIApp,
) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=webapi_with_speech.fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_speech_jobs_requires_admin_auth(speech_client: AsyncClient) -> None:
    resp = await speech_client.post(
        "/api/speech/jobs",
        json={"text": "hi"},
    )
    assert resp.status_code == 401


async def test_speech_jobs_synthesizes_and_returns_artifact_ref(
    speech_client: AsyncClient,
) -> None:
    _StubTtsProvider.calls.clear()
    resp = await speech_client.post(
        "/api/speech/jobs",
        headers={"Authorization": "Bearer admin-secret"},
        json={
            "text": "你好",
            "voice": "default",
            "speed": 1.1,
            "pitch": -1,
            "style": "bright",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["artifact_id"].startswith("spk_")
    assert body["download_url"] == f"/api/media/speech/{body['artifact_id']}"
    assert body["mime_type"] == "audio/wav"
    assert body["size_bytes"] == len(WAV_BYTES)
    assert body["voice"] == "default"
    assert body["provider"] == "stub-tts"

    # The provider saw the request with all forward fields.
    assert len(_StubTtsProvider.calls) == 1
    request = _StubTtsProvider.calls[0]
    assert request.text == "你好"
    assert request.style == "bright"
    assert request.speed == 1.1
    assert request.pitch == -1


async def test_speech_jobs_caches_repeated_requests(
    speech_client: AsyncClient,
) -> None:
    _StubTtsProvider.calls.clear()
    headers = {"Authorization": "Bearer admin-secret"}
    body = {"text": "再来一次", "voice": "default"}

    first = await speech_client.post("/api/speech/jobs", headers=headers, json=body)
    second = await speech_client.post("/api/speech/jobs", headers=headers, json=body)

    assert first.status_code == second.status_code == 200
    # Same inputs → same artifact_id, single synthesis call.
    assert first.json()["artifact_id"] == second.json()["artifact_id"]
    assert len(_StubTtsProvider.calls) == 1


async def test_media_download_streams_audio_bytes(
    speech_client: AsyncClient,
) -> None:
    job = await speech_client.post(
        "/api/speech/jobs",
        headers={"Authorization": "Bearer admin-secret"},
        json={"text": "下载", "voice": "default"},
    )
    artifact_id = job.json()["artifact_id"]

    # Auth required for download too.
    unauth = await speech_client.get(f"/api/media/speech/{artifact_id}")
    assert unauth.status_code == 401

    resp = await speech_client.get(
        f"/api/media/speech/{artifact_id}",
        headers={"Authorization": "Bearer admin-secret"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/wav"
    assert resp.content == WAV_BYTES


async def test_media_download_404_for_unknown_or_expired(
    speech_client: AsyncClient,
    webapi_with_speech: WebAPIApp,
) -> None:
    headers = {"Authorization": "Bearer admin-secret"}

    # Unknown id → 404.
    missing = await speech_client.get(
        "/api/media/speech/spk_doesnotexist",
        headers=headers,
    )
    assert missing.status_code == 404

    # Path-traversal attempt is rejected with 400, not 404.
    traversal = await speech_client.get(
        "/api/media/speech/..%2f..%2fetc%2fpasswd",
        headers=headers,
    )
    assert traversal.status_code in (400, 404)

    # Expire the artifact in-place and verify it disappears.
    job = await speech_client.post(
        "/api/speech/jobs",
        headers=headers,
        json={"text": "expired soon"},
    )
    artifact_id = job.json()["artifact_id"]
    store = webapi_with_speech.speech_artifact_store
    assert store is not None
    # Force-expire by mutating the in-memory entry.
    for entry in store._index.values():  # type: ignore[attr-defined]
        if entry.artifact_id == artifact_id:
            entry.expires_at = 0.0
    expired = await speech_client.get(
        f"/api/media/speech/{artifact_id}",
        headers=headers,
    )
    assert expired.status_code == 404


async def test_speech_jobs_passes_provider_errors_through(
    speech_client: AsyncClient,
) -> None:
    _StubTtsProvider.fail_with = "stub provider offline"
    try:
        resp = await speech_client.post(
            "/api/speech/jobs",
            headers={"Authorization": "Bearer admin-secret"},
            json={"text": "失败", "voice": "default"},
        )
        assert resp.status_code == 502
        body = resp.json()
        assert body["detail"]["code"] == "tts_stub_error"
        assert "stub provider offline" in body["detail"]["message"]
    finally:
        _StubTtsProvider.fail_with = ""


async def test_speech_disabled_returns_503(tmp_path: Path) -> None:
    mock = _make_mock_app(speech_enabled=False)
    app = WebAPIApp(
        application=mock,
        host="127.0.0.1",
        port=6185,
        auth_token="admin-secret",
    )
    transport = ASGITransport(app=app.fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/api/speech/jobs",
            headers={"Authorization": "Bearer admin-secret"},
            json={"text": "x"},
        )
        assert resp.status_code == 503
