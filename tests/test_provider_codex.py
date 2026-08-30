from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response as WebSocketResponse

from nahida_bot.agent.context import ContextMessage
from nahida_bot.agent.providers.base import (
    ProviderRequestContext,
    current_provider_request_context,
)
from nahida_bot.agent.providers.codex import CodexProvider, _parse_quota_windows
from nahida_bot.agent.providers.errors import ProviderAuthError
from nahida_bot.db.repositories.sqlite_codex_token_repo import CodexToken


class _FakeResponse:
    def __init__(
        self,
        body: dict[str, object],
        status_code: int = 200,
        text: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self._body = body
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def json(self) -> dict[str, object]:
        return self._body

    @property
    def content(self) -> bytes:
        return self.text.encode("utf-8")

    async def aread(self) -> bytes:
        return self.text.encode("utf-8")

    async def aiter_lines(self):
        for line in self.text.splitlines():
            yield line


class _FakeStream:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response

    async def __aenter__(self) -> _FakeResponse:
        return self.response

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


class _FakeWebSocket:
    def __init__(
        self,
        events: list[dict[str, object]],
        *,
        response_headers: dict[str, str] | None = None,
    ) -> None:
        self._events = [json.dumps(event) for event in events]
        self.response = MagicMock(headers=response_headers or {})
        self.sent: list[dict[str, object]] = []

    async def __aenter__(self) -> _FakeWebSocket:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))


class _FakeClient:
    is_closed = False

    def __init__(self, response: _FakeResponse | list[_FakeResponse]) -> None:
        self._responses = response if isinstance(response, list) else [response]
        self.request_count = 0
        self.request_headers: list[dict[str, str]] = []
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
        self._record_request(url=url, headers=headers, payload=json)
        return self._next_response()

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> _FakeResponse:
        del timeout
        self._record_request(url=url, headers=headers, payload={})
        return self._next_response()

    def stream(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> _FakeStream:
        del method
        del timeout
        self._record_request(url=url, headers=headers, payload=json)
        return _FakeStream(self._next_response())

    def _record_request(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
    ) -> None:
        self.last_url = url
        self.last_headers = headers
        self.last_payload = payload
        self.request_headers.append(dict(headers))

    def _next_response(self) -> _FakeResponse:
        index = min(self.request_count, len(self._responses) - 1)
        self.request_count += 1
        return self._responses[index]


def _build_provider(token: CodexToken | None) -> tuple[CodexProvider, MagicMock]:
    repo = MagicMock()
    repo.get = AsyncMock(return_value=token)
    repo.upsert = AsyncMock(return_value=None)

    provider = CodexProvider(
        db_engine=MagicMock(),
        stream_responses=False,
        websocket_responses=False,
    )
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
async def test_chat_refreshes_and_retries_once_after_401(monkeypatch) -> None:  # noqa: ANN001
    token = CodexToken(
        refresh_token="OLD-REFRESH",
        access_token="OLD-ACCESS",
        expires_at=int(time.time()) + 10_000,
        account_id="ACCT",
    )
    provider, repo = _build_provider(token=token)
    provider._client = _FakeClient(  # type: ignore[attr-defined]
        [
            _FakeResponse({}, status_code=401, text="expired"),
            _FakeResponse({"output": [], "status": "completed", "usage": {}}),
        ]
    )

    from nahida_bot.auth.codex import TokenResponse

    refresh = AsyncMock(
        return_value=TokenResponse(
            access_token="NEW-ACCESS",
            refresh_token="NEW-REFRESH",
            id_token="",
            expires_in=3600,
        )
    )
    monkeypatch.setattr(
        "nahida_bot.agent.providers.codex.refresh_access_token", refresh
    )

    await provider.chat(messages=_sample_message())

    client = provider._client  # type: ignore[attr-defined]
    assert client.request_count == 2  # type: ignore[attr-defined]
    assert client.request_headers[0]["Authorization"] == "Bearer OLD-ACCESS"  # type: ignore[attr-defined]
    assert client.request_headers[1]["Authorization"] == "Bearer NEW-ACCESS"  # type: ignore[attr-defined]
    refresh.assert_awaited_once()
    repo.upsert.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_chat_refreshes_and_retries_once_after_401(monkeypatch) -> None:  # noqa: ANN001
    token = CodexToken(
        refresh_token="OLD-REFRESH",
        access_token="OLD-ACCESS",
        expires_at=int(time.time()) + 10_000,
        account_id="ACCT",
    )
    provider, _ = _build_provider(token=token)
    provider.stream_responses = True
    completed = (
        'data: {"type":"response.completed","response":'
        '{"output":[],"status":"completed","usage":{}}}'
    )
    provider._client = _FakeClient(  # type: ignore[attr-defined]
        [
            _FakeResponse({}, status_code=401, text="expired"),
            _FakeResponse({}, text=completed),
        ]
    )

    from nahida_bot.auth.codex import TokenResponse

    refresh = AsyncMock(
        return_value=TokenResponse(
            access_token="NEW-ACCESS",
            refresh_token="NEW-REFRESH",
            id_token="",
            expires_in=3600,
        )
    )
    monkeypatch.setattr(
        "nahida_bot.agent.providers.codex.refresh_access_token", refresh
    )

    await provider.chat(messages=_sample_message())

    client = provider._client  # type: ignore[attr-defined]
    assert client.request_count == 2  # type: ignore[attr-defined]
    assert client.request_headers[1]["Authorization"] == "Bearer NEW-ACCESS"  # type: ignore[attr-defined]
    assert client.request_headers[1]["Accept"] == "text/event-stream"  # type: ignore[attr-defined]
    refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_websocket_stream_sends_current_codex_envelope_and_headers(
    monkeypatch,  # noqa: ANN001
) -> None:
    token = CodexToken(
        refresh_token="REFRESH",
        access_token="ACCESS",
        expires_at=int(time.time()) + 10_000,
        account_id="ACCT",
    )
    provider, _ = _build_provider(token=token)
    provider.stream_responses = True
    provider.websocket_responses = True
    websocket = _FakeWebSocket(
        [
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_ws",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "hello"}],
                        }
                    ],
                    "usage": {},
                },
            }
        ],
        response_headers={"x-codex-turn-state": "turn-state-1"},
    )
    connection: dict[str, object] = {}

    def connect(uri: str, **kwargs):  # noqa: ANN003
        connection["uri"] = uri
        connection["headers"] = kwargs["additional_headers"]
        return websocket

    monkeypatch.setattr("nahida_bot.agent.providers.codex.websocket_connect", connect)
    context_token = current_provider_request_context.set(
        ProviderRequestContext(session_id="milky:group:123", request_id="request-1")
    )
    try:
        response = await provider.chat(messages=_sample_message())
        next_headers = provider._build_headers(token)  # type: ignore[attr-defined]
    finally:
        current_provider_request_context.reset(context_token)

    headers = connection["headers"]
    assert isinstance(headers, dict)
    assert connection["uri"] == "wss://chatgpt.com/backend-api/codex/responses"
    assert headers["OpenAI-Beta"] == "responses_websockets=2026-02-06"
    assert headers["x-client-request-id"] == "request-1"
    assert headers["session-id"] == headers["thread-id"]
    assert next_headers["x-codex-turn-state"] == "turn-state-1"
    assert websocket.sent[0]["type"] == "response.create"
    assert websocket.sent[0]["include"] == ["reasoning.encrypted_content"]
    assert response.content == "hello"


@pytest.mark.asyncio
async def test_websocket_handshake_failure_falls_back_to_sse(monkeypatch) -> None:  # noqa: ANN001
    token = CodexToken(
        refresh_token="REFRESH",
        access_token="ACCESS",
        expires_at=int(time.time()) + 10_000,
        account_id="ACCT",
    )
    provider, _ = _build_provider(token=token)
    provider.stream_responses = True
    provider.websocket_responses = True
    completed = (
        'data: {"type":"response.completed","response":'
        '{"output":[],"status":"completed","usage":{}}}'
    )
    provider._client = _FakeClient(_FakeResponse({}, text=completed))  # type: ignore[attr-defined]

    def fail_connect(uri: str, **kwargs):  # noqa: ANN003
        del uri
        del kwargs
        raise OSError("proxy does not support websocket")

    monkeypatch.setattr(
        "nahida_bot.agent.providers.codex.websocket_connect", fail_connect
    )

    await provider.chat(messages=_sample_message())

    assert provider._client.request_count == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_websocket_401_refreshes_before_single_retry(monkeypatch) -> None:  # noqa: ANN001
    token = CodexToken(
        refresh_token="OLD-REFRESH",
        access_token="OLD-ACCESS",
        expires_at=int(time.time()) + 10_000,
        account_id="ACCT",
    )
    provider, _ = _build_provider(token=token)
    provider.stream_responses = True
    provider.websocket_responses = True
    websocket = _FakeWebSocket(
        [
            {
                "type": "response.completed",
                "response": {"output": [], "status": "completed", "usage": {}},
            }
        ]
    )
    attempts = 0

    def connect(uri: str, **kwargs):  # noqa: ANN003
        nonlocal attempts
        del uri
        del kwargs
        attempts += 1
        if attempts == 1:
            raise InvalidStatus(
                WebSocketResponse(401, "Unauthorized", Headers(), b"expired")
            )
        return websocket

    from nahida_bot.auth.codex import TokenResponse

    refresh = AsyncMock(
        return_value=TokenResponse(
            access_token="NEW-ACCESS",
            refresh_token="NEW-REFRESH",
            id_token="",
            expires_in=3600,
        )
    )
    monkeypatch.setattr("nahida_bot.agent.providers.codex.websocket_connect", connect)
    monkeypatch.setattr(
        "nahida_bot.agent.providers.codex.refresh_access_token", refresh
    )

    await provider.chat(messages=_sample_message())

    assert attempts == 2
    refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_chat_does_not_retry_403(monkeypatch) -> None:  # noqa: ANN001
    token = CodexToken(
        refresh_token="REFRESH",
        access_token="ACCESS",
        expires_at=int(time.time()) + 10_000,
        account_id="ACCT",
    )
    provider, _ = _build_provider(token=token)
    provider._client = _FakeClient(  # type: ignore[attr-defined]
        _FakeResponse({}, status_code=403, text="forbidden")
    )
    refresh = AsyncMock()
    monkeypatch.setattr(
        "nahida_bot.agent.providers.codex.refresh_access_token", refresh
    )

    with pytest.raises(ProviderAuthError) as exc_info:
        await provider.chat(messages=_sample_message())

    assert exc_info.value.status_code == 403
    assert provider._client.request_count == 1  # type: ignore[attr-defined]
    refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_stops_after_second_401(monkeypatch) -> None:  # noqa: ANN001
    token = CodexToken(
        refresh_token="OLD-REFRESH",
        access_token="OLD-ACCESS",
        expires_at=int(time.time()) + 10_000,
        account_id="ACCT",
    )
    provider, _ = _build_provider(token=token)
    provider._client = _FakeClient(  # type: ignore[attr-defined]
        [
            _FakeResponse({}, status_code=401, text="expired"),
            _FakeResponse({}, status_code=401, text="still rejected"),
        ]
    )

    from nahida_bot.auth.codex import TokenResponse

    refresh = AsyncMock(
        return_value=TokenResponse(
            access_token="NEW-ACCESS",
            refresh_token="NEW-REFRESH",
            id_token="",
            expires_in=3600,
        )
    )
    monkeypatch.setattr(
        "nahida_bot.agent.providers.codex.refresh_access_token", refresh
    )

    with pytest.raises(ProviderAuthError) as exc_info:
        await provider.chat(messages=_sample_message())

    assert exc_info.value.status_code == 401
    assert provider._client.request_count == 2  # type: ignore[attr-defined]
    refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrent_401s_share_one_token_refresh(monkeypatch) -> None:  # noqa: ANN001
    token = CodexToken(
        refresh_token="OLD-REFRESH",
        access_token="OLD-ACCESS",
        expires_at=int(time.time()) + 10_000,
        account_id="ACCT",
    )
    provider, repo = _build_provider(token=token)
    provider._cached_token = token  # type: ignore[attr-defined]

    from nahida_bot.auth.codex import TokenResponse

    async def refresh_once(*args, **kwargs):  # noqa: ANN002, ANN003
        del args
        del kwargs
        await asyncio.sleep(0)
        return TokenResponse(
            access_token="NEW-ACCESS",
            refresh_token="NEW-REFRESH",
            id_token="",
            expires_in=3600,
        )

    refresh = AsyncMock(side_effect=refresh_once)
    monkeypatch.setattr(
        "nahida_bot.agent.providers.codex.refresh_access_token", refresh
    )

    first, second = await asyncio.gather(
        provider._refresh_after_unauthorized("OLD-ACCESS"),  # type: ignore[attr-defined]
        provider._refresh_after_unauthorized("OLD-ACCESS"),  # type: ignore[attr-defined]
    )

    assert first.access_token == second.access_token == "NEW-ACCESS"
    refresh.assert_awaited_once()
    repo.upsert.assert_awaited_once()


@pytest.mark.asyncio
async def test_quota_refreshes_and_retries_once_after_401(monkeypatch) -> None:  # noqa: ANN001
    token = CodexToken(
        refresh_token="OLD-REFRESH",
        access_token="OLD-ACCESS",
        expires_at=int(time.time()) + 10_000,
        account_id="ACCT",
    )
    provider, _ = _build_provider(token=token)
    provider._client = _FakeClient(  # type: ignore[attr-defined]
        [
            _FakeResponse({}, status_code=401, text="expired"),
            _FakeResponse(
                {
                    "rate_limit": {
                        "primary_window": {
                            "used_percent": 25,
                            "limit_window_seconds": 18000,
                        }
                    }
                }
            ),
        ]
    )

    from nahida_bot.auth.codex import TokenResponse

    refresh = AsyncMock(
        return_value=TokenResponse(
            access_token="NEW-ACCESS",
            refresh_token="NEW-REFRESH",
            id_token="",
            expires_in=3600,
        )
    )
    monkeypatch.setattr(
        "nahida_bot.agent.providers.codex.refresh_access_token", refresh
    )

    snapshot = await provider.query_quota(provider_id="codex")

    client = provider._client  # type: ignore[attr-defined]
    assert client.request_count == 2  # type: ignore[attr-defined]
    assert client.request_headers[1]["Authorization"] == "Bearer NEW-ACCESS"  # type: ignore[attr-defined]
    assert snapshot.windows[0].percent_remaining == 75.0
    refresh.assert_awaited_once()


def test_parse_quota_windows_supports_current_multi_limit_shape() -> None:
    windows = _parse_quota_windows(
        {
            "rate_limits_by_limit_id": {
                "codex": {
                    "primary": {
                        "remaining_percent": 80,
                        "limit_window_seconds": 18000,
                        "reset_after_seconds": 60,
                    },
                    "secondary": {
                        "used_percent": 35,
                        "limit_window_seconds": 604800,
                    },
                },
                "review": {
                    "limit_name": "Review",
                    "rate_limit": {
                        "primary_window": {
                            "used_percent": 10,
                            "limit_window_seconds": 2592000,
                        }
                    },
                },
            }
        }
    )

    assert [(window.name, window.percent_remaining) for window in windows] == [
        ("5h", 80.0),
        ("Weekly", 65.0),
        ("Review 30d", 90.0),
    ]


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
