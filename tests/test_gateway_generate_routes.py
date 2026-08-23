"""Tests for POST /api/generate/text (persona-grounded generation)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from nahida_bot.gateway.routes.generate import router as generate_router
from nahida_bot.speech.base import SpeechArtifact
from nahida_bot.workspace.manager import WorkspaceManager


class _FakeChatResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeProvider:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    async def chat(self, messages: Any, model: str = "") -> _FakeChatResponse:
        self.calls.append({"messages": list(messages), "model": model})
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
        self.calls: list[dict[str, Any]] = []

    def resolve_for_task(
        self,
        task: str,
        *,
        explicit: str = "",
        default_spec: str = "",
        fallback: str = "disabled",
    ) -> _FakeRouted | None:
        self.calls.append(
            {
                "task": task,
                "explicit": explicit,
                "default_spec": default_spec,
                "fallback": fallback,
            }
        )
        return self._routed


class _FakeSettings:
    def __init__(self, system_prompt: str = "系统基线提示词") -> None:
        self.system_prompt = system_prompt


class _FakeApplication:
    def __init__(
        self,
        model_router: Any,
        settings: Any = None,
        workspace_manager: Any = None,
    ) -> None:
        self._model_router = model_router
        self.settings = settings
        self.workspace_manager = workspace_manager

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


def _make_workspace_manager(
    tmp_path: Path,
    soul: str = "# 测试灵魂\n温柔而清醒。",
    user: str | None = "# 用户画像\n喜欢简洁。",
) -> WorkspaceManager:
    manager = WorkspaceManager(tmp_path)
    manager.initialize()
    root = manager.workspace_path(manager.get_active_workspace().workspace_id)
    (root / "SOUL.md").write_text(soul, encoding="utf-8")
    if user is not None:
        (root / "USER.md").write_text(user, encoding="utf-8")
    return manager


def _build_app(
    provider: _FakeProvider | None,
    *,
    speech: bool = False,
    generate_model: str | None = None,
    workspace_manager: Any = None,
    system_prompt: str = "系统基线提示词",
) -> tuple[FastAPI, _FakeSpeechService | None]:
    app = FastAPI()
    routed = _FakeRouted(_FakeSlot(provider)) if provider else None
    app.state.application = _FakeApplication(
        _FakeModelRouter(routed),
        settings=_FakeSettings(system_prompt),
        workspace_manager=workspace_manager,
    )
    if generate_model is not None:
        app.state.generate_config = type(
            "GenerateConfig", (), {"model": generate_model}
        )()
    speech_service = None
    if speech:
        speech_service = _FakeSpeechService()
        app.state.speech_service = speech_service
        app.state.speech_artifact_store = _FakeSpeechStore()
        app.state.speech_config = type(
            "Config", (), {"max_text_length": 500, "max_concurrency": 1}
        )()
    app.include_router(generate_router)
    return app, speech_service


@pytest.mark.asyncio
async def test_injects_workspace_persona_prefix(tmp_path: Path) -> None:
    manager = _make_workspace_manager(tmp_path)
    provider = _FakeProvider(["“休息一下吧，眼睛也要放个假哦”"])
    app, _ = _build_app(provider, workspace_manager=manager)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/generate/text",
            json={
                "prompt": "场景：专注时段刚刚开始。生成一句提醒。",
                "avoid": ["上一句用过的"],
                "synthesize": False,
            },
        )

    assert response.status_code == 200
    assert response.json()["text"] == "休息一下吧，眼睛也要放个假哦"

    messages = provider.calls[0]["messages"]
    system_contents = [m.content for m in messages if m.role == "system"]
    user_contents = [m.content for m in messages if m.role == "user"]
    assert system_contents[0] == "系统基线提示词"
    assert any("温柔而清醒" in content for content in system_contents)
    assert any("喜欢简洁" in content for content in system_contents)
    assert len(user_contents) == 1
    assert "场景：专注时段刚刚开始" in user_contents[0]
    assert "上一句用过的" in user_contents[0]


@pytest.mark.asyncio
async def test_uses_request_workspace_when_specified(tmp_path: Path) -> None:
    manager = _make_workspace_manager(tmp_path)
    manager.create_workspace("alt")
    alt_root = manager.workspace_path("alt")
    (alt_root / "SOUL.md").write_text(
        "# 备用人格\n冷静克制。",
        encoding="utf-8",
    )
    provider = _FakeProvider(["好了"])
    app, _ = _build_app(provider, workspace_manager=manager)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/generate/text",
            json={"prompt": "生成一句", "workspace": "alt", "synthesize": False},
        )

    assert response.status_code == 200
    system_contents = [
        m.content for m in provider.calls[0]["messages"] if m.role == "system"
    ]
    assert any("冷静克制" in content for content in system_contents)
    assert not any("温柔而清醒" in content for content in system_contents)


@pytest.mark.asyncio
async def test_unknown_workspace_returns_404(tmp_path: Path) -> None:
    manager = _make_workspace_manager(tmp_path)
    provider = _FakeProvider(["好了"])
    app, _ = _build_app(provider, workspace_manager=manager)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/generate/text",
            json={"prompt": "生成一句", "workspace": "no-such"},
        )

    assert response.status_code == 404
    assert provider.calls == []


@pytest.mark.asyncio
async def test_generates_without_workspace_manager() -> None:
    provider = _FakeProvider(["没有灵魂也能干活"])
    app, _ = _build_app(provider, workspace_manager=None)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/generate/text",
            json={"prompt": "生成一句", "synthesize": False},
        )

    assert response.status_code == 200
    assert response.json()["text"] == "没有灵魂也能干活"
    messages = provider.calls[0]["messages"]
    system_contents = [m.content for m in messages if m.role == "system"]
    assert system_contents == ["系统基线提示词"]


@pytest.mark.asyncio
async def test_model_spec_defaults_to_primary_tag_with_default_fallback() -> None:
    provider = _FakeProvider(["休息一下吧"])
    app, _ = _build_app(provider)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/generate/text",
            json={"prompt": "生成一句", "synthesize": False},
        )

    assert response.status_code == 200
    application = app.state.application
    assert application.model_router.calls == [
        {
            "task": "text_generate",
            "explicit": "",
            "default_spec": "primary",
            "fallback": "default",
        }
    ]


@pytest.mark.asyncio
async def test_request_model_spec_wins_over_gateway_config() -> None:
    provider = _FakeProvider(["休息一下吧"])
    app, _ = _build_app(provider, generate_model="cheap")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/generate/text",
            json={"prompt": "生成一句", "model": "zai/glm-5.2", "synthesize": False},
        )

    assert response.status_code == 200
    application = app.state.application
    assert application.model_router.calls[0]["explicit"] == "zai/glm-5.2"


@pytest.mark.asyncio
async def test_empty_request_model_falls_back_to_gateway_config() -> None:
    provider = _FakeProvider(["休息一下吧"])
    app, _ = _build_app(provider, generate_model="cheap")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/generate/text",
            json={"prompt": "生成一句", "model": "", "synthesize": False},
        )

    assert response.status_code == 200
    application = app.state.application
    assert application.model_router.calls[0]["explicit"] == "cheap"


@pytest.mark.asyncio
async def test_avoids_recently_used_lines_with_retry() -> None:
    provider = _FakeProvider(["上一句用过的", "这句是全新的提醒"])
    app, _ = _build_app(provider)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/generate/text",
            json={"prompt": "生成一句", "avoid": ["上一句用过的"], "synthesize": False},
        )

    assert response.status_code == 200
    assert response.json()["text"] == "这句是全新的提醒"
    assert len(provider.calls) == 2


@pytest.mark.asyncio
async def test_cleans_quotes_and_clamps_to_max_chars() -> None:
    provider = _FakeProvider(["“  这一句很长很长很长很长很长很长很长  ”"])
    app, _ = _build_app(provider)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/generate/text",
            json={"prompt": "生成一句", "max_chars": 5, "synthesize": False},
        )

    assert response.status_code == 200
    assert response.json()["text"] == "这一句很长"


@pytest.mark.asyncio
async def test_pre_synthesizes_speech_with_requested_style() -> None:
    provider = _FakeProvider(["新的一轮专注开始啦，加油！"])
    app, speech_service = _build_app(provider, speech=True)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/generate/text",
            json={
                "prompt": "生成一句",
                "synthesize": True,
                "style": "cheerful",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["speech"] is not None
    assert body["speech"]["artifact_id"] == "art-1"
    assert speech_service is not None
    assert speech_service.calls[0]["text"] == "新的一轮专注开始啦，加油！"
    assert speech_service.calls[0]["style"] == "cheerful"


@pytest.mark.asyncio
async def test_returns_503_when_no_model_router() -> None:
    app, _ = _build_app(None)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/generate/text",
            json={"prompt": "生成一句"},
        )

    assert response.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"prompt": ""},
        {"prompt": "x" * 4001},
        {"prompt": "生成一句", "max_chars": 0},
        {"prompt": "生成一句", "max_chars": 201},
        {"prompt": "生成一句", "avoid": ["x" * 201]},
        {"prompt": "生成一句", "avoid": [f"line-{i}" for i in range(13)]},
        {"prompt": "生成一句", "model": "m" * 129},
        {"prompt": "生成一句", "style": "s" * 65},
    ],
)
async def test_rejects_invalid_payloads(payload: dict[str, Any]) -> None:
    provider = _FakeProvider(["好的"])
    app, _ = _build_app(provider)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/generate/text", json=payload)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_returns_502_when_generation_fails_or_is_empty() -> None:
    provider = _FakeProvider([])
    app, _ = _build_app(provider)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/generate/text",
            json={"prompt": "生成一句", "synthesize": False},
        )

    assert response.status_code == 502

    empty_provider = _FakeProvider(["   ", ""])
    app2, _ = _build_app(empty_provider)
    async with AsyncClient(
        transport=ASGITransport(app=app2), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/generate/text",
            json={"prompt": "生成一句", "synthesize": False},
        )

    assert response.status_code == 502
