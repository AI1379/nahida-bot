"""Tests for POST /api/pomodoro/reminders (dynamic reminder generation)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from nahida_bot.gateway.routes.pomodoro import router as pomodoro_router
from nahida_bot.speech.base import SpeechArtifact


class _FakeChatResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeProvider:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    async def chat(self, messages: Any, model: str = "") -> _FakeChatResponse:
        self.calls.append({"messages": messages, "model": model})
        if not self.replies:
            raise RuntimeError("no scripted reply")
        return _FakeChatResponse(self.replies.pop(0))


class _FakeSlot:
    def __init__(self, provider: _FakeProvider) -> None:
        self.provider = provider
        self.default_model = "test-model"


class _FakeRouted:
    def __init__(self, slot: _FakeSlot) -> None:
        self.slot = slot
        self.model = ""


class _FakeModelRouter:
    def __init__(self, routed: _FakeRouted | None) -> None:
        self._routed = routed

    def resolve_for_task(
        self,
        task: str,
        *,
        explicit: str = "",
        default_spec: str = "",
        fallback: str = "disabled",
    ) -> _FakeRouted | None:
        return self._routed


class _FakeApplication:
    def __init__(self, model_router: Any) -> None:
        self._model_router = model_router

    @property
    def model_router(self) -> Any:
        return self._model_router


class _FakeSpeechService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def resolve_provider_type(self, voice: str) -> str:
        return "stub-tts"

    async def synthesize(self, text: str, **kwargs: Any) -> SpeechArtifact:
        self.calls.append({"text": text, **kwargs})
        return SpeechArtifact(
            data=b"RIFF",
            mime_type="audio/wav",
            duration_ms=100,
            provider="stub-tts",
            voice="stub",
        )


class _FakeStoredArtifact:
    def to_public_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": "art-1",
            "mime_type": "audio/wav",
            "size_bytes": 4,
            "duration_ms": 100,
            "voice": "stub",
            "provider": "stub-tts",
            "expires_at": "2026-01-01T00:00:00Z",
        }


class _FakeSpeechStore:
    async def find_cached(self, **kwargs: Any) -> None:
        return None

    async def put(self, **kwargs: Any) -> _FakeStoredArtifact:
        return _FakeStoredArtifact()


def _build_app(
    provider: _FakeProvider | None,
    *,
    speech: bool = False,
) -> tuple[FastAPI, _FakeSpeechService | None]:
    app = FastAPI()
    routed = _FakeRouted(_FakeSlot(provider)) if provider else None
    app.state.application = _FakeApplication(_FakeModelRouter(routed))
    speech_service = None
    if speech:
        speech_service = _FakeSpeechService()
        app.state.speech_service = speech_service
        app.state.speech_artifact_store = _FakeSpeechStore()
        app.state.speech_config = type(
            "Config", (), {"max_text_length": 500, "max_concurrency": 1}
        )()
    app.include_router(pomodoro_router)
    return app, speech_service


@pytest.mark.asyncio
async def test_generates_text_without_speech_services() -> None:
    provider = _FakeProvider(["“休息一下吧，眼睛也要放个假哦”"])
    app, _ = _build_app(provider, speech=False)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/pomodoro/reminders",
            json={"phase": "break_start", "avoid": [], "synthesize": True},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["phase"] == "break_start"
    assert body["text"] == "休息一下吧，眼睛也要放个假哦"
    assert body["speech"] is None


@pytest.mark.asyncio
async def test_avoids_recently_used_lines_with_retry() -> None:
    provider = _FakeProvider(["上一句用过的", "这句是全新的提醒"])
    app, _ = _build_app(provider)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/pomodoro/reminders",
            json={"phase": "work_start", "avoid": ["上一句用过的"]},
        )

    assert response.status_code == 200
    assert response.json()["text"] == "这句是全新的提醒"
    assert len(provider.calls) == 2


@pytest.mark.asyncio
async def test_pre_synthesizes_speech_with_desktop_style() -> None:
    provider = _FakeProvider(["新的一轮专注开始啦，加油！"])
    app, speech_service = _build_app(provider, speech=True)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/pomodoro/reminders",
            json={"phase": "work_start", "synthesize": True},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["speech"] is not None
    assert body["speech"]["artifact_id"] == "art-1"
    assert speech_service is not None
    assert speech_service.calls[0]["text"] == "新的一轮专注开始啦，加油！"
    # Must mirror the Desktop reminder segment voice params so the
    # /api/speech/jobs cache key matches at trigger time.
    assert speech_service.calls[0]["style"] == "neutral"


@pytest.mark.asyncio
async def test_generates_rounds_done_phase_text() -> None:
    provider = _FakeProvider(["全部搞定啦，去喝杯水庆祝一下吧"])
    app, _ = _build_app(provider)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/pomodoro/reminders",
            json={"phase": "rounds_done"},
        )

    assert response.status_code == 200
    assert response.json()["text"] == "全部搞定啦，去喝杯水庆祝一下吧"


@pytest.mark.asyncio
async def test_returns_503_when_no_model_router() -> None:
    app, _ = _build_app(None)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/pomodoro/reminders",
            json={"phase": "break_end"},
        )

    assert response.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"phase": "explode"},
        {"phase": "work_start", "avoid": ["x" * 201]},
        {"phase": "work_start", "avoid": [f"line-{i}" for i in range(13)]},
    ],
)
async def test_rejects_invalid_payloads(payload: dict[str, Any]) -> None:
    provider = _FakeProvider(["好的"])
    app, _ = _build_app(provider)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/pomodoro/reminders",
            json=payload,
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_returns_502_when_generation_fails_or_is_empty() -> None:
    provider = _FakeProvider([])
    app, _ = _build_app(provider)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/pomodoro/reminders",
            json={"phase": "work_start"},
        )

    assert response.status_code == 502

    empty_provider = _FakeProvider(["   ", ""])
    app2, _ = _build_app(empty_provider)
    async with AsyncClient(
        transport=ASGITransport(app=app2), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/pomodoro/reminders",
            json={"phase": "work_start"},
        )

    assert response.status_code == 502
