"""Tests for the Codex subscription Images API client and plugin dispatch."""

from __future__ import annotations

import base64
from typing import Any

import httpx
import pytest

from nahida_bot.plugins.image_generation.client import (
    CodexImageGenerationClient,
    ImageGenerationError,
)
from nahida_bot.plugins.image_generation.config import (
    CodexImagesBackendConfig,
    ImageGenerationConfig,
    parse_image_generation_config,
)
from nahida_bot.plugins.image_generation.plugin import ImageGenerationPlugin

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)
PNG_B64 = base64.b64encode(PNG_1X1).decode("ascii")


class _FakeToken:
    def __init__(self, access_token: str, account_id: str = "ACCT-123") -> None:
        self.access_token = access_token
        self.account_id = account_id


class _FakeTransport:
    def __init__(self, response_body: dict[str, Any], status: int = 200) -> None:
        self._body = response_body
        self._status = status
        self.last_request: httpx.Request | None = None

    async def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.last_request = request
        return httpx.Response(
            self._status,
            json=self._body,
            request=request,
        )


def _mock_monkeypatch_openai_default(monkeypatch, transport: _FakeTransport) -> None:
    """Make the client's httpx.AsyncClient use our transport."""
    transport_handler = transport.handle_request
    real_async_client = httpx.AsyncClient

    class _PatchedClient(real_async_client):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs.setdefault("transport", httpx.MockTransport(transport_handler))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(
        "nahida_bot.plugins.image_generation.client.httpx.AsyncClient",
        _PatchedClient,
    )


@pytest.mark.asyncio
async def test_generate_sends_subscription_headers_and_default_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _FakeTransport(
        {"data": [{"b64_json": PNG_B64}], "size": "auto", "quality": "auto"}
    )
    _mock_monkeypatch_openai_default(monkeypatch, transport)

    config = CodexImagesBackendConfig()
    _mock_monkeypatch_openai_default(monkeypatch, transport)

    def _token() -> _FakeToken:
        return _FakeToken("ACCESS-TOKEN")

    client = CodexImageGenerationClient(config, _async_resolver(_token()))
    images = await client.generate("a red fox")

    assert len(images) == 1
    assert images[0].data == PNG_1X1

    request = transport.last_request
    assert request is not None
    assert request.url == "https://chatgpt.com/backend-api/codex/images/generations"
    assert request.headers["Authorization"] == "Bearer ACCESS-TOKEN"
    assert request.headers["ChatGPT-Account-Id"] == "ACCT-123"
    assert request.headers["originator"] == "nahida-bot"
    assert request.headers["User-Agent"].startswith("nahida-bot/")

    import json

    payload = json.loads(request.content)
    assert payload["prompt"] == "a red fox"
    assert payload["model"] == "gpt-image-2"
    assert payload["size"] == "auto"
    assert payload["quality"] == "auto"
    assert payload["background"] == "auto"
    assert payload["n"] == 1


@pytest.mark.asyncio
async def test_generate_overrides_model_size_quality_from_call_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _FakeTransport({"data": [{"b64_json": PNG_B64}]})
    _mock_monkeypatch_openai_default(monkeypatch, transport)

    config = CodexImagesBackendConfig(max_images_per_request=5)
    client = CodexImageGenerationClient(config, _async_resolver(_FakeToken("A")))

    await client.generate(
        "fox",
        model="gpt-image-1.5",
        size="1536x1024",
        quality="high",
        n=2,
    )

    import json

    payload = json.loads(transport.last_request.content)  # type: ignore[union-attr]
    assert payload["model"] == "gpt-image-1.5"
    assert payload["size"] == "1536x1024"
    assert payload["quality"] == "high"
    assert payload["n"] == 2


@pytest.mark.asyncio
async def test_generate_omits_account_id_header_when_token_has_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _FakeTransport({"data": [{"b64_json": PNG_B64}]})
    _mock_monkeypatch_openai_default(monkeypatch, transport)

    client = CodexImageGenerationClient(
        CodexImagesBackendConfig(),
        _async_resolver(_FakeToken("A", account_id="")),
    )
    await client.generate("fox")

    assert "ChatGPT-Account-Id" not in transport.last_request.headers  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_generate_raises_auth_error_on_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _FakeTransport({"error": {"message": "bad token"}}, status=401)
    _mock_monkeypatch_openai_default(monkeypatch, transport)

    client = CodexImageGenerationClient(
        CodexImagesBackendConfig(),
        _async_resolver(_FakeToken("expired")),
    )
    with pytest.raises(ImageGenerationError) as exc_info:
        await client.generate("fox")
    assert exc_info.value.code == "image_generation_auth_failed"
    assert not exc_info.value.retryable


@pytest.mark.asyncio
async def test_generate_raises_rate_limited_on_429(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _FakeTransport({"error": {"message": "slow down"}}, status=429)
    _mock_monkeypatch_openai_default(monkeypatch, transport)

    client = CodexImageGenerationClient(
        CodexImagesBackendConfig(),
        _async_resolver(_FakeToken("A")),
    )
    with pytest.raises(ImageGenerationError) as exc_info:
        await client.generate("fox")
    assert exc_info.value.code == "image_generation_rate_limited"
    assert exc_info.value.retryable


@pytest.mark.asyncio
async def test_generate_raises_bad_response_when_no_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _FakeTransport({"created": 1})
    _mock_monkeypatch_openai_default(monkeypatch, transport)

    client = CodexImageGenerationClient(
        CodexImagesBackendConfig(),
        _async_resolver(_FakeToken("A")),
    )
    with pytest.raises(ImageGenerationError) as exc_info:
        await client.generate("fox")
    assert exc_info.value.code == "image_generation_bad_response"


@pytest.mark.asyncio
async def test_generate_raises_on_empty_prompt() -> None:
    client = CodexImageGenerationClient(
        CodexImagesBackendConfig(),
        _async_resolver(_FakeToken("A")),
    )
    with pytest.raises(ImageGenerationError) as exc_info:
        await client.generate("   ")
    assert exc_info.value.code == "image_generation_empty_prompt"


@pytest.mark.asyncio
async def test_generate_passes_custom_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _FakeTransport({"data": [{"b64_json": PNG_B64}]})
    _mock_monkeypatch_openai_default(monkeypatch, transport)

    config = CodexImagesBackendConfig(base_url="https://custom.example.com/codex")
    client = CodexImageGenerationClient(config, _async_resolver(_FakeToken("A")))
    await client.generate("fox")

    assert (
        str(transport.last_request.url)  # type: ignore[union-attr]
        == "https://custom.example.com/codex/images/generations"
    )


def _async_resolver(token: Any):
    async def _resolve() -> Any:
        return token

    return _resolve


# ── Plugin-level token resolver dispatch tests ──


class _StubAPI:
    """Minimal plugin api stub exposing get_provider_manager()."""

    def __init__(self, manager: Any | None) -> None:
        self._manager = manager
        from nahida_bot_sdk.testing import RecordingMockBotAPI

        self.logger = RecordingMockBotAPI().logger

    def get_provider_manager(self) -> Any | None:
        return self._manager


class _StubSlot:
    def __init__(self, provider: Any) -> None:
        self.provider = provider


class _StubProvider:
    def __init__(self, has_resolver: bool = True) -> None:
        self._has_resolver = has_resolver

    async def _resolve_token(self) -> _FakeToken:
        return _FakeToken("FROM-PROVIDER")


class _StubManager:
    def __init__(self, slots: dict[str, _StubSlot]) -> None:
        self._slots = slots

    def get(self, provider_id: str) -> _StubSlot | None:
        return self._slots.get(provider_id)


def _make_plugin(manager: Any | None) -> ImageGenerationPlugin:
    config = ImageGenerationConfig(
        provider="codex",
        backends={"codex": CodexImagesBackendConfig()},
    )
    manifest = type(
        "M",
        (),
        {"config": config.model_dump()},
    )()
    plugin = ImageGenerationPlugin(_StubAPI(manager), manifest)  # type: ignore[arg-type]
    return plugin


@pytest.mark.asyncio
async def test_token_resolver_raises_when_manager_missing() -> None:
    plugin = _make_plugin(manager=None)
    backend = CodexImagesBackendConfig()
    resolver = plugin._build_codex_token_resolver(backend)
    with pytest.raises(ImageGenerationError) as exc_info:
        await resolver()
    assert exc_info.value.code == "image_generation_not_configured"
    assert "Provider manager" in exc_info.value.message


@pytest.mark.asyncio
async def test_token_resolver_raises_when_codex_slot_missing() -> None:
    plugin = _make_plugin(manager=_StubManager({}))
    backend = CodexImagesBackendConfig(provider_id="codex")
    resolver = plugin._build_codex_token_resolver(backend)
    with pytest.raises(ImageGenerationError) as exc_info:
        await resolver()
    assert "auth login codex" in exc_info.value.message


@pytest.mark.asyncio
async def test_token_resolver_raises_when_provider_not_codex() -> None:
    plugin = _make_plugin(
        manager=_StubManager({"codex": _StubSlot(provider=object())})  # type: ignore[arg-type]
    )
    backend = CodexImagesBackendConfig(provider_id="codex")
    resolver = plugin._build_codex_token_resolver(backend)
    with pytest.raises(ImageGenerationError) as exc_info:
        await resolver()
    assert "not a Codex provider" in exc_info.value.message


@pytest.mark.asyncio
async def test_token_resolver_delegates_to_codex_provider() -> None:
    plugin = _make_plugin(
        manager=_StubManager({"codex": _StubSlot(provider=_StubProvider())})  # type: ignore[arg-type]
    )
    backend = CodexImagesBackendConfig(provider_id="codex")
    resolver = plugin._build_codex_token_resolver(backend)
    token = await resolver()
    assert token.access_token == "FROM-PROVIDER"


def test_config_parses_codex_images_backend() -> None:
    config = parse_image_generation_config(
        {
            "provider": "codex",
            "backends": {
                "codex": {
                    "type": "codex-images",
                    "provider_id": "codex",
                    "model": "gpt-image-2",
                    "size": "1024x1024",
                    "quality": "high",
                }
            },
        }
    )
    backend = config.backend("codex")
    assert isinstance(backend, CodexImagesBackendConfig)
    assert backend.provider_id == "codex"
    assert backend.model == "gpt-image-2"
    assert backend.size == "1024x1024"
    assert backend.quality == "high"
    assert backend.base_url == "https://chatgpt.com/backend-api/codex"
