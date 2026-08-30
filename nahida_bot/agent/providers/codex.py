"""ChatGPT Codex provider — uses a ChatGPT Plus/Pro OAuth subscription.

This provider reuses ``OpenAIResponsesProvider``'s request/response
serialization (the Codex backend speaks the Responses API wire format)
but swaps in:

- OAuth bearer auth (refreshed on demand) instead of a static API key
- ``ChatGPT-Account-Id`` header identifying the Plus/Pro account
- An ``originator`` header so OpenAI can attribute traffic honestly
- A URL rewrite from ``{base_url}/responses`` to the Codex backend at
  ``https://chatgpt.com/backend-api/codex/responses``

Tokens live in the ``codex_tokens`` SQLite table (one row per configured
provider id). Login happens out-of-band via ``nahida-bot auth login codex``;
if no token is present the provider raises ``ProviderAuthError`` on the
first request instead of failing at startup.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
import structlog
from websockets.asyncio.client import connect as websocket_connect
from websockets.exceptions import InvalidStatus, WebSocketException

from nahida_bot.agent.providers.base import current_provider_request_context
from nahida_bot.agent.providers.errors import (
    ProviderAuthError,
    ProviderTimeoutError,
    ProviderTransportError,
)
from nahida_bot.agent.providers.openai_responses import OpenAIResponsesProvider
from nahida_bot.agent.providers.quota import (
    QuotaQueryError,
    QuotaSnapshot,
    QuotaWindow,
)
from nahida_bot.agent.providers.registry import register_provider
from nahida_bot.auth import (
    CODEX_API_ENDPOINT,
    extract_account_id,
    refresh_access_token,
    resolve_client_id,
    resolve_originator,
    token_needs_refresh,
    user_agent,
)
from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.db.repositories.sqlite_codex_token_repo import (
    CodexToken,
    SQLiteCodexTokenRepository,
)
from nahida_bot.version import get_version

logger = structlog.get_logger(__name__)

_CODEX_WEBSOCKET_BETA = "responses_websockets=2026-02-06"
_MAX_TURN_STATES = 256
_QUOTA_COLLECTION_KEYS = (
    "rate_limits_by_limit_id",
    "rate_limits",
    "additional_rate_limits",
)


class _CodexWebSocketUnavailable(Exception):
    """The WebSocket handshake failed before a request was accepted."""


def _quota_limit_containers(
    body: dict[str, object],
) -> list[tuple[str, dict[str, object]]]:
    containers: list[tuple[str, dict[str, object]]] = []
    primary = body.get("rate_limit")
    if isinstance(primary, dict):
        containers.append(("", primary))

    for collection_key in _QUOTA_COLLECTION_KEYS:
        collection = body.get(collection_key)
        if isinstance(collection, dict):
            if any(
                key in collection
                for key in (
                    "primary",
                    "secondary",
                    "primary_window",
                    "secondary_window",
                )
            ):
                containers.append(("", collection))
                continue
            entries = collection.items()
        elif isinstance(collection, list):
            entries = ((str(index), value) for index, value in enumerate(collection))
        else:
            continue

        for identifier, value in entries:
            if not isinstance(value, dict):
                continue
            nested = value.get("rate_limit")
            container = nested if isinstance(nested, dict) else value
            label_raw = value.get("limit_name") or value.get("name") or identifier
            label = label_raw if isinstance(label_raw, str) else str(identifier)
            containers.append((label, container))
    return containers


def _quota_window_name(seconds: object) -> str:
    if seconds == 18000:
        return "5h"
    if seconds == 604800:
        return "Weekly"
    if seconds == 2592000:
        return "30d"
    return f"{seconds}s" if isinstance(seconds, int) else "Quota"


def _quota_reset_at(window: dict[str, object]) -> str | None:
    reset_at = window.get("reset_at") or window.get("resets_at")
    if isinstance(reset_at, str) and reset_at:
        return reset_at
    if isinstance(reset_at, (int, float)) and not isinstance(reset_at, bool):
        timestamp = float(reset_at)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        if timestamp > 0:
            return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()
    reset_after = window.get("reset_after_seconds")
    if isinstance(reset_after, (int, float)) and not isinstance(reset_after, bool):
        return datetime.fromtimestamp(
            time.time() + float(reset_after), tz=UTC
        ).isoformat()
    return None


def _parse_quota_windows(body: dict[str, object]) -> tuple[QuotaWindow, ...]:
    windows: list[QuotaWindow] = []
    seen: set[tuple[str, float, str | None]] = set()
    for label, container in _quota_limit_containers(body):
        for key in ("primary_window", "secondary_window", "primary", "secondary"):
            window = container.get(key)
            if not isinstance(window, dict):
                continue
            remaining = window.get("remaining_percent")
            if not isinstance(remaining, (int, float)) or isinstance(remaining, bool):
                used = window.get("used_percent")
                if not isinstance(used, (int, float)) or isinstance(used, bool):
                    continue
                remaining = 100.0 - float(used)
            percent_remaining = max(0.0, min(100.0, float(remaining)))
            seconds = window.get("limit_window_seconds") or window.get("window_seconds")
            name = _quota_window_name(seconds)
            if label and label not in {"codex", "default", "0"}:
                name = f"{label} {name}"
            reset_at = _quota_reset_at(window)
            identity = (name, percent_remaining, reset_at)
            if identity in seen:
                continue
            seen.add(identity)
            windows.append(
                QuotaWindow(
                    name=name,
                    percent_remaining=percent_remaining,
                    reset_at=reset_at,
                )
            )
    return tuple(windows)


@register_provider("codex", "ChatGPT Codex (Plus/Pro subscription) Provider")
@dataclass(slots=True)
class CodexProvider(OpenAIResponsesProvider):
    """Responses-API provider that authenticates with ChatGPT OAuth tokens.

    The parent class fields ``base_url`` / ``api_key`` are kept for factory
    compatibility (``create_provider`` always passes them) but are not used
    for auth — ``api_key`` is ignored and ``base_url`` only matters if the
    caller overrides the Codex backend URL (rare; for testing).
    """

    base_url: str = CODEX_API_ENDPOINT
    api_key: str = ""
    model: str = "gpt-5.5"
    name: str = "codex"
    api_family: str = "codex"
    stream_responses: bool = True
    websocket_responses: bool = True
    websocket_fallback: bool = True
    db_engine: DatabaseEngine | None = None
    _token_repo: SQLiteCodexTokenRepository | None = field(
        default=None, init=False, repr=False
    )
    _cached_token: CodexToken | None = field(default=None, init=False, repr=False)
    _refresh_lock: asyncio.Lock | None = field(default=None, init=False, repr=False)
    _turn_states: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _log_namespace = "codex"

    def _repo(self) -> SQLiteCodexTokenRepository:
        if self._token_repo is None:
            if self.db_engine is None:
                raise ProviderAuthError(
                    "CodexProvider requires a database engine; the application "
                    "must inject one at construction time."
                )
            self._token_repo = SQLiteCodexTokenRepository(self.db_engine)
        return self._token_repo

    def _refresh_lock_obj(self) -> asyncio.Lock:
        if self._refresh_lock is None:
            self._refresh_lock = asyncio.Lock()
        return self._refresh_lock

    async def _resolve_token(self) -> CodexToken:
        """Return a token with a usable access token, refreshing if needed.

        Refresh is serialized to avoid the thundering-herd problem when
        many concurrent requests notice an expired token at once.
        """
        if self._cached_token is None:
            self._cached_token = await self._repo().get(self.name)
        token = self._cached_token
        if token is None:
            raise ProviderAuthError(
                f"No Codex OAuth token for provider '{self.name}'. "
                f"Run `nahida-bot auth login {self.name}` first."
            )
        if not token_needs_refresh(token):
            return token

        async with self._refresh_lock_obj():
            if self._cached_token is not None and not token_needs_refresh(
                self._cached_token
            ):
                return self._cached_token
            refreshed = await self._do_refresh(token)
            await self._repo().upsert(self.name, refreshed)
            self._cached_token = refreshed
            logger.info(
                "provider.codex.token_refreshed",
                provider_name=self.name,
                account_id=refreshed.account_id,
            )
            return refreshed

    async def _do_refresh(self, token: CodexToken) -> CodexToken:
        client = self._ensure_client()
        try:
            response = await refresh_access_token(
                client, token.refresh_token, client_id=resolve_client_id()
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Codex token refresh timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderTransportError("Codex token refresh request failed") from exc
        except RuntimeError as exc:
            raise ProviderAuthError(
                f"Failed to refresh Codex access token: {exc}"
            ) from exc
        expires_at = int(time.time()) + max(response.expires_in, 0)
        return CodexToken(
            refresh_token=response.refresh_token,
            access_token=response.access_token,
            expires_at=expires_at,
            account_id=extract_account_id(response) or token.account_id,
        )

    async def _refresh_after_unauthorized(
        self, rejected_access_token: str
    ) -> CodexToken:
        """Refresh a server-rejected token, deduplicating concurrent retries."""
        async with self._refresh_lock_obj():
            cached = self._cached_token
            if (
                cached is not None
                and cached.access_token != rejected_access_token
                and not token_needs_refresh(cached)
            ):
                return cached

            stored = await self._repo().get(self.name)
            if (
                stored is not None
                and stored.access_token != rejected_access_token
                and not token_needs_refresh(stored)
            ):
                self._cached_token = stored
                return stored

            source = stored or cached
            if source is None:
                raise ProviderAuthError(
                    f"No Codex OAuth token for provider '{self.name}'. "
                    f"Run `nahida-bot auth login {self.name}` first."
                )

            refreshed = await self._do_refresh(source)
            await self._repo().upsert(self.name, refreshed)
            self._cached_token = refreshed
            logger.info(
                "provider.codex.token_refreshed",
                provider_name=self.name,
                account_id=refreshed.account_id,
                reason="unauthorized",
            )
            return refreshed

    async def _headers_after_unauthorized(
        self, rejected_headers: dict[str, str]
    ) -> dict[str, str]:
        authorization = rejected_headers.get("Authorization", "")
        prefix = "Bearer "
        rejected_access_token = (
            authorization[len(prefix) :] if authorization.startswith(prefix) else ""
        )
        token = await self._refresh_after_unauthorized(rejected_access_token)
        headers = dict(rejected_headers)
        headers.update(self._build_headers(token))
        if not token.account_id:
            headers.pop("ChatGPT-Account-Id", None)
        return headers

    async def _post_json(
        self,
        *,
        endpoint: str,
        payload: dict[str, object],
        headers: dict[str, str],
        timeout: float,
        invalid_json_message: str = "Provider returned non-JSON body",
    ) -> tuple[dict[str, object], int]:
        try:
            return await super(CodexProvider, self)._post_json(
                endpoint=endpoint,
                payload=payload,
                headers=headers,
                timeout=timeout,
                invalid_json_message=invalid_json_message,
            )
        except ProviderAuthError as exc:
            if exc.status_code != 401:
                raise
        retry_headers = await self._headers_after_unauthorized(headers)
        logger.warning(
            "provider.codex.request_retry",
            provider_name=self.name,
            reason="unauthorized",
        )
        return await super(CodexProvider, self)._post_json(
            endpoint=endpoint,
            payload=payload,
            headers=retry_headers,
            timeout=timeout,
            invalid_json_message=invalid_json_message,
        )

    async def _stream_responses(
        self,
        *,
        client: httpx.AsyncClient,
        endpoint: str,
        payload: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> dict[str, object]:
        try:
            return await self._stream_with_fallback(
                client=client,
                endpoint=endpoint,
                payload=payload,
                headers=headers,
                timeout=timeout,
            )
        except ProviderAuthError as exc:
            if exc.status_code != 401:
                raise
        retry_headers = await self._headers_after_unauthorized(headers)
        logger.warning(
            "provider.codex.request_retry",
            provider_name=self.name,
            reason="unauthorized",
        )
        return await self._stream_with_fallback(
            client=client,
            endpoint=endpoint,
            payload=payload,
            headers=retry_headers,
            timeout=timeout,
        )

    async def _stream_with_fallback(
        self,
        *,
        client: httpx.AsyncClient,
        endpoint: str,
        payload: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> dict[str, object]:
        if self.websocket_responses:
            try:
                return await self._websocket_response(
                    endpoint=endpoint,
                    payload=payload,
                    headers=headers,
                    timeout=timeout,
                )
            except _CodexWebSocketUnavailable as exc:
                if not self.websocket_fallback:
                    raise ProviderTransportError(
                        "Codex WebSocket connection unavailable"
                    ) from exc
                logger.warning(
                    "provider.codex.websocket_fallback",
                    provider_name=self.name,
                    error_type=type(exc.__cause__ or exc).__name__,
                )
        return await super(CodexProvider, self)._stream_responses(
            client=client,
            endpoint=endpoint,
            payload=payload,
            headers=headers,
            timeout=timeout,
        )

    async def _websocket_response(
        self,
        *,
        endpoint: str,
        payload: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> dict[str, object]:
        websocket_url = self._websocket_url(endpoint)
        websocket_headers = dict(headers)
        websocket_headers.pop("Accept", None)
        websocket_headers["OpenAI-Beta"] = _CODEX_WEBSOCKET_BETA
        request_sent = False

        try:
            async with asyncio.timeout(timeout):
                async with websocket_connect(
                    websocket_url,
                    additional_headers=websocket_headers,
                    user_agent_header=None,
                    open_timeout=min(timeout, 10.0),
                    close_timeout=5.0,
                    max_size=16 * 1024 * 1024,
                ) as websocket:
                    handshake_response = websocket.response
                    if handshake_response is not None:
                        self._observe_response_headers(
                            handshake_response.headers,
                            request_headers=websocket_headers,
                        )
                    envelope = {"type": "response.create", **payload}
                    await websocket.send(json.dumps(envelope, ensure_ascii=False))
                    request_sent = True

                    async def events() -> AsyncIterator[dict[str, object]]:
                        async for raw_event in websocket:
                            if isinstance(raw_event, bytes):
                                raw_event = raw_event.decode("utf-8", errors="replace")
                            try:
                                event = json.loads(raw_event)
                            except json.JSONDecodeError:
                                continue
                            if isinstance(event, dict):
                                yield event

                    return await self._parse_stream_events(events())
        except InvalidStatus as exc:
            status_code = exc.response.status_code
            if status_code in (401, 403):
                body = exc.response.body.decode("utf-8", errors="replace")
                raise ProviderAuthError(
                    "Provider auth rejected WebSocket request with status "
                    f"{status_code} — {body[:200]}",
                    status_code=status_code,
                ) from exc
            raise _CodexWebSocketUnavailable from exc
        except TimeoutError as exc:
            if not request_sent:
                raise _CodexWebSocketUnavailable from exc
            raise ProviderTimeoutError("Codex WebSocket response timed out") from exc
        except (OSError, WebSocketException) as exc:
            if not request_sent:
                raise _CodexWebSocketUnavailable from exc
            raise ProviderTransportError("Codex WebSocket stream failed") from exc

    @staticmethod
    def _websocket_url(endpoint: str) -> str:
        if endpoint.startswith("https://"):
            return f"wss://{endpoint.removeprefix('https://')}"
        if endpoint.startswith("http://"):
            return f"ws://{endpoint.removeprefix('http://')}"
        return endpoint

    async def _resolve_auth(
        self,
        payload: dict[str, object],
        headers: dict[str, str],
    ) -> dict[str, str]:
        """Refresh Codex OAuth credentials without duplicating request assembly."""
        del headers
        raw_include = payload.get("include")
        if isinstance(raw_include, list):
            if "reasoning.encrypted_content" not in raw_include:
                payload["include"] = [*raw_include, "reasoning.encrypted_content"]
        elif raw_include is None:
            payload["include"] = ["reasoning.encrypted_content"]
        token = await self._resolve_token()
        resolved_headers = self._build_headers(token)
        if self.stream_responses:
            resolved_headers["Accept"] = "text/event-stream"
        return resolved_headers

    def _request_transport_log_fields(self, endpoint: str) -> dict[str, object]:
        """Keep Codex endpoint and account identity visible in request logs."""
        account_id = self._cached_token.account_id if self._cached_token else ""
        return {"endpoint": endpoint, "account_id": account_id}

    def _build_headers(self, token: CodexToken) -> dict[str, str]:
        request_context = current_provider_request_context.get()
        headers = {
            "Authorization": f"Bearer {token.access_token}",
            "Content-Type": "application/json",
            "originator": resolve_originator(),
            "User-Agent": user_agent(),
            "Version": get_version(),
            "x-client-request-id": request_context.request_id or str(uuid4()),
        }
        if token.account_id:
            headers["ChatGPT-Account-Id"] = token.account_id
        if request_context.session_id:
            thread_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"nahida-bot:{self.name}:{request_context.session_id}",
                )
            )
            headers["session-id"] = thread_id
            headers["thread-id"] = thread_id
            turn_state = self._turn_states.get(thread_id)
            if turn_state:
                headers["x-codex-turn-state"] = turn_state
        return headers

    def _observe_response_headers(
        self,
        response_headers: Mapping[str, str],
        *,
        request_headers: Mapping[str, str],
    ) -> None:
        thread_id = request_headers.get("thread-id", "")
        turn_state = response_headers.get("x-codex-turn-state", "")
        if not thread_id or not turn_state:
            return
        if (
            thread_id not in self._turn_states
            and len(self._turn_states) >= _MAX_TURN_STATES
        ):
            self._turn_states.pop(next(iter(self._turn_states)))
        self._turn_states[thread_id] = turn_state

    async def query_quota(
        self, *, provider_id: str, force_refresh: bool = False
    ) -> QuotaSnapshot:
        """Query the ChatGPT subscription rate-limit windows."""
        del force_refresh
        token = await self._resolve_token()
        client = self._ensure_client()
        headers = self._build_headers(token)
        headers["User-Agent"] = "codex-cli"
        response = await self._request_quota(client, headers)
        if response.status_code == 401:
            headers = await self._headers_after_unauthorized(headers)
            logger.warning(
                "provider.codex.request_retry",
                provider_name=self.name,
                reason="quota_unauthorized",
            )
            response = await self._request_quota(client, headers)

        if response.status_code in (401, 403):
            raise QuotaQueryError("Codex quota authentication failed", "auth")
        if response.status_code == 429 or response.status_code >= 500:
            raise QuotaQueryError(
                "Codex quota service temporarily unavailable", "transient"
            )
        if response.status_code >= 400:
            raise QuotaQueryError(
                f"Codex quota request failed (HTTP {response.status_code})", "request"
            )
        if len(response.content) > 256 * 1024:
            raise QuotaQueryError("Codex quota response is too large", "parse")
        try:
            body = response.json()
        except ValueError as exc:
            raise QuotaQueryError(
                "Codex quota response was not valid JSON", "parse"
            ) from exc
        if not isinstance(body, dict):
            raise QuotaQueryError("Codex quota response was not an object", "parse")
        windows = _parse_quota_windows(body)
        if not windows:
            raise QuotaQueryError("Codex returned no quota windows", "parse")
        plan_type = body.get("plan_type")
        plan_name = (
            f"ChatGPT {plan_type.replace('_', ' ').title()}"
            if isinstance(plan_type, str) and plan_type
            else "ChatGPT subscription"
        )
        return QuotaSnapshot(
            provider_id=provider_id,
            provider_label=self.name,
            adapter="codex-subscription",
            plan_name=plan_name,
            windows=windows,
            queried_at=datetime.now(UTC).isoformat(),
        )

    @staticmethod
    async def _request_quota(
        client: httpx.AsyncClient, headers: dict[str, str]
    ) -> httpx.Response:
        try:
            return await client.get(
                "https://chatgpt.com/backend-api/wham/usage",
                headers=headers,
                timeout=15.0,
            )
        except httpx.TimeoutException as exc:
            raise QuotaQueryError("Codex quota request timed out", "transient") from exc
        except httpx.HTTPError as exc:
            raise QuotaQueryError(
                "Codex quota network request failed", "transient"
            ) from exc

    def _resolve_endpoint(self) -> str:
        # Allow base_url override (mainly for tests). When left at the
        # default, the Codex backend URL is used verbatim — no /responses
        # suffix because CODEX_API_ENDPOINT already points at it.
        if self.base_url and self.base_url != CODEX_API_ENDPOINT:
            return f"{self.base_url.rstrip('/')}/responses"
        return CODEX_API_ENDPOINT
