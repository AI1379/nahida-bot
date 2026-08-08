from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from nahida_bot.agent.context import ContextMessage
from nahida_bot.agent.providers.codex import CodexProvider
from nahida_bot.agent.providers.errors import ProviderAuthError
from nahida_bot.db.repositories.sqlite_codex_token_repo import CodexToken


class _FakeResponse:
    def __init__(self, body: dict[str, object], status_code: int = 200) -> None:
        self._body = body
        self.status_code = status_code
        self.text = ""

    def json(self) -> dict[str, object]:
        return self._body

    async def aread(self) -> bytes:
        return b""


class _FakeClient:
    is_closed = False

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.last_url: str | None = None
        self.last_headers: dict[str, str] | None = None
        self.last_payload: dict[str, object] | None = None

    async def post(
        self,
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> _FakeResponse:
        self.last_url = url
        self.last_headers = headers
        self.last_payload = json
        return self._response


def _build_provider(token: CodexToken | None) -> tuple[CodexProvider, MagicMock]:
    repo = MagicMock()
    repo.get = AsyncMock(return_value=token)
    repo.upsert = AsyncMock(return_value=None)

    provider = CodexProvider(db_engine=MagicMock())
    provider._token_repo = repo  # type: ignore[attr-defined]
    fake_client = _FakeResponse({"output": [], "status": "completed", "usage": {}})
    client = _FakeClient(fake_client)
    provider._client = client  # type: ignore[attr-defined]
    return provider, repo


def _sample_message() -> list[ContextMessage]:
    return [ContextMessage(role="user", source="test", content="hello")]


@pytest.mark.asyncio
async def test_chat_raises_auth_error_when_no_token_stored() -> None:
    provider, _ = _build_provider(token=None)

    with pytest.raises(ProviderAuthError, match="No Codex OAuth token"):
        await provider.chat(messages=_sample_message())


@pytest.mark.asyncio
async def test_chat_sends_request_to_codex_backend_endpoint() -> None:
    token = CodexToken(
        refresh_token="r",
        access_token="ACCESS-TOKEN",
        expires_at=int(time.time()) + 10_000,
        account_id="ACCT-123",
    )
    provider, _ = _build_provider(token=token)

    await provider.chat(messages=_sample_message())

    client = provider._client  # type: ignore[attr-defined]
    assert client.last_url == "https://chatgpt.com/backend-api/codex/responses"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_chat_sets_authorization_account_id_originator_headers() -> None:
    token = CodexToken(
        refresh_token="r",
        access_token="ACCESS-TOKEN",
        expires_at=int(time.time()) + 10_000,
        account_id="ACCT-123",
    )
    provider, _ = _build_provider(token=token)

    await provider.chat(messages=_sample_message())

    headers = provider._client.last_headers  # type: ignore[attr-defined]
    assert headers is not None
    assert headers["Authorization"] == "Bearer ACCESS-TOKEN"
    assert headers["ChatGPT-Account-Id"] == "ACCT-123"
    assert headers["originator"] == "nahida-bot"
    assert headers["User-Agent"].startswith("nahida-bot/")


@pytest.mark.asyncio
async def test_chat_omits_account_id_header_when_empty() -> None:
    token = CodexToken(
        refresh_token="r",
        access_token="ACCESS-TOKEN",
        expires_at=int(time.time()) + 10_000,
        account_id="",
    )
    provider, _ = _build_provider(token=token)

    await provider.chat(messages=_sample_message())

    headers = provider._client.last_headers  # type: ignore[attr-defined]
    assert "ChatGPT-Account-Id" not in headers


@pytest.mark.asyncio
async def test_chat_refreshes_expired_token_before_request() -> None:
    expired = CodexToken(
        refresh_token="OLD-REFRESH",
        access_token="old-access",
        expires_at=int(time.time()) - 100,
        account_id="ACCT",
    )
    provider, repo = _build_provider(token=expired)

    from nahida_bot.auth.codex import TokenResponse

    refreshed_response = TokenResponse(
        access_token="NEW-ACCESS",
        refresh_token="NEW-REFRESH",
        id_token="",
        expires_in=3600,
    )

    import nahida_bot.agent.providers.codex as codex_module

    original_refresh = codex_module.refresh_access_token
    codex_module.refresh_access_token = AsyncMock(return_value=refreshed_response)  # type: ignore[assignment]
    try:
        await provider.chat(messages=_sample_message())
    finally:
        codex_module.refresh_access_token = original_refresh  # type: ignore[assignment]

    repo.upsert.assert_awaited_once()
    args, _ = repo.upsert.call_args
    persisted_token: CodexToken = args[1]
    assert persisted_token.access_token == "NEW-ACCESS"
    assert persisted_token.refresh_token == "NEW-REFRESH"
    assert persisted_token.account_id == "ACCT"

    headers = provider._client.last_headers  # type: ignore[attr-defined]
    assert headers["Authorization"] == "Bearer NEW-ACCESS"


@pytest.mark.asyncio
async def test_chat_refresh_failure_raises_auth_error() -> None:
    expired = CodexToken(
        refresh_token="r",
        access_token="",
        expires_at=0,
        account_id="ACCT",
    )
    provider, _ = _build_provider(token=expired)

    import nahida_bot.agent.providers.codex as codex_module

    original_refresh = codex_module.refresh_access_token
    codex_module.refresh_access_token = AsyncMock(side_effect=RuntimeError("HTTP 401"))  # type: ignore[assignment]
    try:
        with pytest.raises(ProviderAuthError, match="Failed to refresh"):
            await provider.chat(messages=_sample_message())
    finally:
        codex_module.refresh_access_token = original_refresh  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_chat_serializes_payload_with_codex_model() -> None:
    token = CodexToken(
        refresh_token="r",
        access_token="ACCESS",
        expires_at=int(time.time()) + 10_000,
        account_id="ACCT",
    )
    provider, _ = _build_provider(token=token)

    await provider.chat(messages=_sample_message())

    payload = provider._client.last_payload  # type: ignore[attr-defined]
    assert payload is not None
    assert payload["model"] == "gpt-5.5"
    assert isinstance(payload["input"], list)


def test_resolve_endpoint_uses_default_codex_url() -> None:
    provider = CodexProvider()
    assert (
        provider._resolve_endpoint()
        == "https://chatgpt.com/backend-api/codex/responses"
    )


def test_resolve_endpoint_appends_responses_when_base_url_overridden() -> None:
    provider = CodexProvider(base_url="https://test.example.com")
    assert provider._resolve_endpoint() == "https://test.example.com/responses"
