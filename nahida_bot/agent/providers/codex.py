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
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
import structlog

from nahida_bot.agent.providers.errors import ProviderAuthError
from nahida_bot.agent.providers.openai_responses import OpenAIResponsesProvider
from nahida_bot.agent.providers.quota import (
    QuotaQueryError,
    QuotaSnapshot,
    QuotaWindow,
)
from nahida_bot.agent.providers.registry import register_provider
from nahida_bot.auth import (
    CODEX_API_ENDPOINT,
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

logger = structlog.get_logger(__name__)


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
    db_engine: DatabaseEngine | None = None
    _token_repo: SQLiteCodexTokenRepository | None = field(
        default=None, init=False, repr=False
    )
    _cached_token: CodexToken | None = field(default=None, init=False, repr=False)
    _refresh_lock: asyncio.Lock | None = field(default=None, init=False, repr=False)
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
        except RuntimeError as exc:
            raise ProviderAuthError(
                f"Failed to refresh Codex access token: {exc}"
            ) from exc
        expires_at = int(time.time()) + max(response.expires_in, 0)
        return CodexToken(
            refresh_token=response.refresh_token,
            access_token=response.access_token,
            expires_at=expires_at,
            account_id=token.account_id,
        )

    async def _resolve_auth(
        self,
        payload: dict[str, object],
        headers: dict[str, str],
    ) -> dict[str, str]:
        """Refresh Codex OAuth credentials without duplicating request assembly."""
        del payload
        del headers
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
        headers = {
            "Authorization": f"Bearer {token.access_token}",
            "Content-Type": "application/json",
            "originator": resolve_originator(),
            "User-Agent": user_agent(),
        }
        if token.account_id:
            headers["ChatGPT-Account-Id"] = token.account_id
        return headers

    async def query_quota(
        self, *, provider_id: str, force_refresh: bool = False
    ) -> QuotaSnapshot:
        """Query the ChatGPT subscription rate-limit windows."""
        del force_refresh
        token = await self._resolve_token()
        client = self._ensure_client()
        headers = self._build_headers(token)
        headers["User-Agent"] = "codex-cli"
        try:
            response = await client.get(
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

        rate_limit = body.get("rate_limit") if isinstance(body, dict) else None
        windows: list[QuotaWindow] = []
        if isinstance(rate_limit, dict):
            for key in ("primary_window", "secondary_window"):
                window = rate_limit.get(key)
                if not isinstance(window, dict):
                    continue
                used = window.get("used_percent")
                seconds = window.get("limit_window_seconds")
                reset_at = window.get("reset_at")
                if not isinstance(used, (int, float)):
                    continue
                if seconds == 18000:
                    name = "5h"
                elif seconds == 604800:
                    name = "Weekly"
                elif seconds == 2592000:
                    name = "30d"
                else:
                    name = f"{seconds}s" if isinstance(seconds, int) else "Quota"
                reset = None
                if isinstance(reset_at, (int, float)) and reset_at > 0:
                    reset = datetime.fromtimestamp(reset_at, tz=UTC).isoformat()
                windows.append(
                    QuotaWindow(
                        name=name,
                        percent_remaining=max(0.0, min(100.0, 100.0 - float(used))),
                        reset_at=reset,
                    )
                )
        if not windows:
            raise QuotaQueryError("Codex returned no quota windows", "parse")
        return QuotaSnapshot(
            provider_id=provider_id,
            provider_label=self.name,
            adapter="codex-subscription",
            plan_name="ChatGPT subscription",
            windows=tuple(windows),
            queried_at=datetime.now(UTC).isoformat(),
        )

    def _resolve_endpoint(self) -> str:
        # Allow base_url override (mainly for tests). When left at the
        # default, the Codex backend URL is used verbatim — no /responses
        # suffix because CODEX_API_ENDPOINT already points at it.
        if self.base_url and self.base_url != CODEX_API_ENDPOINT:
            return f"{self.base_url.rstrip('/')}/responses"
        return CODEX_API_ENDPOINT
