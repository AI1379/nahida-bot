"""Async OpenAPI client for Feishu (tenant_access_token + REST endpoints).

The official SDK's REST client is synchronous (requests-based); this client
mirrors :class:`nahida_bot.channels.milky.client.MilkyClient` so sends stay on
the bot's asyncio loop. Only the WebSocket event stream uses the SDK.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from typing import Any, TypeAlias

import httpx

from nahida_bot.channels.feishu.config import FeishuPluginConfig

JsonDict: TypeAlias = dict[str, Any]

# Business codes that are safe to retry with backoff.
_RETRYABLE_API_CODES = frozenset({230020})  # 230020: frequency limit
# Business codes indicating the tenant_access_token expired or is invalid.
_TOKEN_EXPIRED_API_CODES = frozenset({99991663, 99991661, 99991668})
_TOKEN_ENDPOINT_PATH = "/auth/v3/tenant_access_token/internal"
# Token lifetime is ~7200s; refresh this share before the deadline.
_TOKEN_REFRESH_MARGIN = 0.8


class FeishuClientError(Exception):
    """Base class for Feishu client failures."""


class FeishuClientClosedError(FeishuClientError):
    """The client was used after being closed."""


class FeishuNetworkError(FeishuClientError):
    """Network or timeout failure while calling Feishu."""

    def __init__(self, message: str, *, api: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.api = api
        self.retryable = retryable


class FeishuHTTPStatusError(FeishuClientError):
    """Non-200 HTTP status from the OpenAPI endpoint."""

    def __init__(self, message: str, *, api: str, status_code: int) -> None:
        super().__init__(message)
        self.api = api
        self.status_code = status_code


class FeishuAuthError(FeishuClientError):
    """Feishu rejected the app credentials."""


class FeishuResponseError(FeishuClientError):
    """Malformed response body."""


class FeishuAPIError(FeishuClientError):
    """Feishu returned a valid failed response envelope (code != 0)."""

    def __init__(
        self, message: str, *, api: str, code: int, data: JsonDict | None = None
    ) -> None:
        super().__init__(message)
        self.api = api
        self.code = code
        self.data = data or {}


class FeishuClient:
    """Small async HTTP client for the Feishu OpenAPI."""

    def __init__(
        self,
        config: FeishuPluginConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
        sleep: Any = asyncio.sleep,
    ) -> None:
        self._config = config
        self._client = http_client
        self._owns_client = http_client is None
        self._sleep = sleep
        self._closed = False
        self._token = ""
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    @property
    def config(self) -> FeishuPluginConfig:
        """Client configuration."""
        return self._config

    async def close(self) -> None:
        """Close the underlying connection pool when owned by this client."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
        self._client = None
        self._closed = True

    # ── token management ──────────────────────────────────────────

    async def _access_token(self, *, force_refresh: bool = False) -> str:
        if not self._config.app_id or not self._config.app_secret:
            raise FeishuAuthError(
                "Feishu app_id/app_secret are not configured (feishu.app_id, "
                "feishu.app_secret)"
            )
        now = time.monotonic()
        if not force_refresh and self._token and now < self._token_expires_at:
            return self._token
        async with self._token_lock:
            now = time.monotonic()
            if not force_refresh and self._token and now < self._token_expires_at:
                return self._token

            # The token endpoint returns fields at the top level (no data
            # envelope): {"code":0,"expire":7200,"tenant_access_token":"…"}.
            response = await self._call_api_raw(
                "POST",
                _TOKEN_ENDPOINT_PATH,
                api="tenant_access_token",
                json_body={
                    "app_id": self._config.app_id,
                    "app_secret": self._config.app_secret,
                },
                auth=False,
            )
            try:
                data = response.json()
            except ValueError as exc:
                raise FeishuResponseError(
                    "Feishu token endpoint returned invalid JSON"
                ) from exc
            if not isinstance(data, dict):
                raise FeishuResponseError("Feishu token response must be a JSON object")
            code = _as_int(data.get("code"), default=0)
            if code != 0:
                raise FeishuAuthError(
                    f"Feishu rejected app credentials "
                    f"(code={code} msg={data.get('msg')!r})"
                )
            token = str(data.get("tenant_access_token") or "")
            if not token:
                raise FeishuAuthError(
                    "Feishu token response missing tenant_access_token"
                )
            expire = _as_int(data.get("expire"), default=0) or 7200
            self._token = token
            self._token_expires_at = now + max(60.0, expire * _TOKEN_REFRESH_MARGIN)
            return token

    def invalidate_token(self) -> None:
        """Drop the cached token so the next call fetches a fresh one."""
        self._token = ""
        self._token_expires_at = 0.0

    # ── public API surface ────────────────────────────────────────

    async def get_bot_info(self) -> JsonDict:
        """Fetch the bot's own identity (``GET /bot/v3/info``).

        Tolerates both the standard envelope and a direct-object response —
        this legacy endpoint has shipped both shapes historically.
        """
        response = await self._call_api_raw("GET", "/bot/v3/info", api="bot_info")
        try:
            body = response.json()
        except ValueError as exc:
            raise FeishuResponseError(
                "Feishu API bot_info returned invalid JSON"
            ) from exc
        if not isinstance(body, dict):
            raise FeishuResponseError(
                "Feishu API bot_info response must be a JSON object"
            )
        code = _as_int(body.get("code"), default=0)
        if code != 0:
            raise FeishuAPIError(
                str(body.get("msg") or f"Feishu API bot_info failed (code {code})"),
                api="bot_info",
                code=code,
                data=body,
            )
        data = body.get("data")
        if isinstance(data, dict):
            return data
        # Direct shape: {"open_id": ..., "activate_status": ...}
        if "open_id" in body:
            return body
        return {}

    async def send_message(
        self,
        *,
        receive_id_type: str,
        receive_id: str,
        msg_type: str,
        content: str,
        uuid: str = "",
    ) -> JsonDict:
        """Send one message (``POST /im/v1/messages``) with retry/idempotency."""
        params: dict[str, str] = {"receive_id_type": receive_id_type}
        body: JsonDict = {
            "receive_id": receive_id,
            "msg_type": msg_type,
            "content": content,
        }
        if uuid:
            body["uuid"] = uuid[:50]
        return await self._call_api(
            "POST",
            "/im/v1/messages",
            api="send_message",
            params=params,
            json_body=body,
            retry=True,
        )

    async def reply_message(
        self,
        *,
        message_id: str,
        msg_type: str,
        content: str,
        uuid: str = "",
    ) -> JsonDict:
        """Reply to one message (``POST /im/v1/messages/:id/reply``)."""
        body: JsonDict = {"msg_type": msg_type, "content": content}
        if uuid:
            body["uuid"] = uuid[:50]
        return await self._call_api(
            "POST",
            f"/im/v1/messages/{message_id}/reply",
            api="reply_message",
            json_body=body,
            retry=True,
        )

    async def get_chat_info(self, chat_id: str) -> JsonDict:
        """Fetch chat metadata (``GET /im/v1/chats/:chat_id``)."""
        return await self._call_api(
            "GET",
            f"/im/v1/chats/{chat_id}",
            api="get_chat",
            params={"user_id_type": "open_id"},
        )

    async def get_chat_members(self, chat_id: str) -> list[JsonDict]:
        """Fetch all chat members (``GET /im/v1/chats/:chat_id/members``).

        Paginated; walks every page. The platform does not return bot members.
        """
        members: list[JsonDict] = []
        page_token = ""
        while True:
            params: dict[str, str] = {"member_id_type": "open_id", "page_size": "100"}
            if page_token:
                params["page_token"] = page_token
            data = await self._call_api(
                "GET",
                f"/im/v1/chats/{chat_id}/members",
                api="get_chat_members",
                params=params,
            )
            items = data.get("items")
            if isinstance(items, list):
                members.extend(item for item in items if isinstance(item, dict))
            if not data.get("has_more"):
                break
            page_token = str(data.get("page_token") or "")
            if not page_token:
                break
        return members

    async def upload_image(self, *, data: bytes, file_name: str) -> str:
        """Upload an image for messaging; returns the ``image_key``."""
        result = await self._call_api(
            "POST",
            "/im/v1/images",
            api="upload_image",
            files={
                "image_type": (None, "message"),
                "image": (file_name or "image", data, "application/octet-stream"),
            },
            retry=True,
        )
        return str(result.get("image_key") or "")

    async def upload_file(
        self,
        *,
        data: bytes,
        file_name: str,
        file_type: str,
        duration_ms: int = 0,
    ) -> str:
        """Upload a file for messaging; returns the ``file_key``."""
        form: dict[str, Any] = {
            "file_type": (None, file_type),
            "file_name": (None, file_name or "file"),
        }
        if duration_ms > 0:
            form["duration"] = (None, str(duration_ms))
        form["file"] = (file_name or "file", data, "application/octet-stream")
        result = await self._call_api(
            "POST", "/im/v1/files", api="upload_file", files=form, retry=True
        )
        return str(result.get("file_key") or "")

    async def download_resource(
        self, *, message_id: str, file_key: str, resource_type: str
    ) -> bytes:
        """Download one message resource (image/file/media, ≤100 MB)."""
        response = await self._call_api_raw(
            "GET",
            f"/im/v1/messages/{message_id}/resources/{file_key}",
            api="download_resource",
            params={"type": resource_type},
        )
        return response.content

    # ── request plumbing ──────────────────────────────────────────

    async def _call_api(
        self,
        method: str,
        path: str,
        *,
        api: str,
        params: Mapping[str, str] | None = None,
        json_body: JsonDict | None = None,
        files: dict[str, Any] | None = None,
        retry: bool = False,
    ) -> JsonDict:
        """Call one OpenAPI endpoint with token refresh and retry handling."""
        attempts = self._config.send_retry_attempts if retry else 1
        delay = self._config.send_retry_backoff
        last_error: FeishuClientError | None = None
        for attempt in range(1, attempts + 1):
            try:
                return await self._call_api_once(
                    method,
                    path,
                    api=api,
                    params=params,
                    json_body=json_body,
                    files=files,
                )
            except FeishuAPIError as exc:
                last_error = exc
                if exc.code in _TOKEN_EXPIRED_API_CODES and attempt < attempts:
                    self.invalidate_token()
                    continue
                if exc.code in _RETRYABLE_API_CODES and attempt < attempts:
                    pass
                else:
                    raise
            except FeishuHTTPStatusError as exc:
                last_error = exc
                if (
                    exc.status_code not in {429, *range(500, 600)}
                    or attempt >= attempts
                ):
                    raise
            except FeishuNetworkError as exc:
                last_error = exc
                if not exc.retryable or attempt >= attempts:
                    raise

            await self._sleep(delay)
            delay = delay * 2
        assert last_error is not None
        raise last_error

    async def _call_api_once(
        self,
        method: str,
        path: str,
        *,
        api: str,
        params: Mapping[str, str] | None = None,
        json_body: JsonDict | None = None,
        files: dict[str, Any] | None = None,
    ) -> JsonDict:
        response = await self._call_api_raw(
            method,
            path,
            api=api,
            params=params,
            json_body=json_body,
            files=files,
            auth=True,
        )
        try:
            envelope = response.json()
        except ValueError as exc:
            raise FeishuResponseError(
                f"Feishu API {api} returned invalid JSON"
            ) from exc
        if not isinstance(envelope, dict):
            raise FeishuResponseError(
                f"Feishu API {api} response must be a JSON object"
            )
        code = _as_int(envelope.get("code"), default=0)
        if code != 0:
            raise FeishuAPIError(
                str(envelope.get("msg") or f"Feishu API {api} failed (code {code})"),
                api=api,
                code=code,
                data=envelope,
            )
        data = envelope.get("data")
        return data if isinstance(data, dict) else {}

    async def _call_api_raw(
        self,
        method: str,
        path: str,
        *,
        api: str,
        params: Mapping[str, str] | None = None,
        json_body: JsonDict | None = None,
        files: dict[str, Any] | None = None,
        auth: bool = True,
    ) -> httpx.Response:
        client = self._ensure_client()
        url = f"{self._config.api_base}{path}"
        headers: dict[str, str] = {}
        if auth:
            token = await self._access_token()
            headers["Authorization"] = f"Bearer {token}"

        request_kwargs: dict[str, Any] = {
            "params": dict(params) if params else None,
            "headers": headers,
            "timeout": httpx.Timeout(self._config.connect_timeout),
        }
        if files is not None:
            request_kwargs["files"] = files
        elif json_body is not None:
            request_kwargs["json"] = json_body

        try:
            response = await client.request(method, url, **request_kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise FeishuNetworkError(
                f"Feishu API {api} network failure: {exc}", api=api, retryable=True
            ) from exc
        except httpx.TimeoutException as exc:
            raise FeishuNetworkError(
                f"Feishu API {api} timed out: {exc}", api=api
            ) from exc
        except httpx.RequestError as exc:
            raise FeishuNetworkError(
                f"Feishu API {api} request failed: {exc}", api=api
            ) from exc

        if response.status_code == 401:
            self.invalidate_token()
            raise FeishuAuthError("Feishu tenant_access_token was rejected")
        if response.status_code != 200:
            raise FeishuHTTPStatusError(
                f"Feishu API {api} returned HTTP {response.status_code}",
                api=api,
                status_code=response.status_code,
            )
        return response

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._closed:
            raise FeishuClientClosedError("FeishuClient has been closed")
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._config.connect_timeout)
            )
            self._owns_client = True
        return self._client


def _as_int(value: object, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return default
