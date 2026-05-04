"""HTTP API client for Milky-compatible protocol services."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any, TypeAlias

import httpx

from nahida_bot.channels.milky._parsing import coerce_int
from nahida_bot.channels.milky.config import MilkyPluginConfig
from nahida_bot.channels.milky.segments import (
    IncomingForwardedMessage,
    OutgoingFileUpload,
    OutgoingSegment,
    parse_incoming_forwarded_messages,
)

OutgoingSegmentPayload: TypeAlias = OutgoingSegment | dict[str, Any]


class MilkyClientError(Exception):
    """Base class for Milky HTTP client failures."""


class MilkyClientClosedError(MilkyClientError):
    """The Milky client was used after it had been closed."""


class MilkyNetworkError(MilkyClientError):
    """Network or timeout failure while calling Milky."""

    def __init__(self, message: str, *, api_name: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.api_name = api_name
        self.retryable = retryable


class MilkyAuthError(MilkyClientError):
    """Milky rejected the configured access token."""

    def __init__(self, message: str, *, api_name: str, status_code: int) -> None:
        super().__init__(message)
        self.api_name = api_name
        self.status_code = status_code


class MilkyHTTPStatusError(MilkyClientError):
    """Milky returned a non-200 HTTP status for an API call."""

    def __init__(self, message: str, *, api_name: str, status_code: int) -> None:
        super().__init__(message)
        self.api_name = api_name
        self.status_code = status_code


class MilkyResponseError(MilkyClientError):
    """Milky returned a malformed response envelope."""

    def __init__(self, message: str, *, api_name: str) -> None:
        super().__init__(message)
        self.api_name = api_name


class MilkyAPIError(MilkyClientError):
    """Milky returned a valid failed API response envelope."""

    def __init__(
        self,
        message: str,
        *,
        api_name: str,
        retcode: int,
        data: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.api_name = api_name
        self.retcode = retcode
        self.data = data or {}


class MilkyClient:
    """Small async HTTP client for Milky ``/api/:api`` calls."""

    def __init__(
        self,
        config: MilkyPluginConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
        sleep: Any = asyncio.sleep,
    ) -> None:
        self._config = config
        self._client = http_client
        self._owns_client = http_client is None
        self._sleep = sleep
        self._closed = False

    @property
    def config(self) -> MilkyPluginConfig:
        """Client configuration."""
        return self._config

    async def close(self) -> None:
        """Close the underlying HTTP connection pool if owned by this client."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
        self._client = None
        self._closed = True

    async def post_api(
        self,
        api_name: str,
        payload: dict[str, Any] | None = None,
        *,
        retry: bool = False,
    ) -> dict[str, Any]:
        """POST one Milky API call and return the response ``data`` object."""
        attempts = self._config.send_retry_attempts if retry else 1
        delay = self._config.send_retry_backoff
        last_error: MilkyClientError | None = None

        for attempt in range(1, attempts + 1):
            try:
                return await self._post_api_once(api_name, payload or {})
            except MilkyNetworkError as exc:
                last_error = exc
                if not exc.retryable or attempt >= attempts:
                    raise
            except MilkyHTTPStatusError as exc:
                last_error = exc
                if exc.status_code != 429 or attempt >= attempts:
                    raise

            await self._sleep(delay)
            delay = min(delay * 2, self._config.reconnect_max_delay)

        assert last_error is not None
        raise last_error

    async def get_login_info(self) -> dict[str, Any]:
        """Call ``get_login_info``."""
        return await self.post_api("get_login_info", {})

    async def get_impl_info(self) -> dict[str, Any]:
        """Call ``get_impl_info``."""
        return await self.post_api("get_impl_info", {})

    async def send_private_message(
        self,
        user_id: int,
        message: Sequence[OutgoingSegmentPayload],
    ) -> dict[str, Any]:
        """Send a private message and return Milky send metadata."""
        return await self.post_api(
            "send_private_message",
            {"user_id": user_id, "message": _serialize_message(message)},
            retry=True,
        )

    async def send_group_message(
        self,
        group_id: int,
        message: Sequence[OutgoingSegmentPayload],
    ) -> dict[str, Any]:
        """Send a group message and return Milky send metadata."""
        return await self.post_api(
            "send_group_message",
            {"group_id": group_id, "message": _serialize_message(message)},
            retry=True,
        )

    async def get_resource_temp_url(self, resource_id: str) -> str:
        """Return a temporary URL for a Milky resource ID."""
        data = await self.post_api(
            "get_resource_temp_url", {"resource_id": resource_id}
        )
        return str(data.get("url", ""))

    async def get_forwarded_messages(
        self, forward_id: str
    ) -> list[IncomingForwardedMessage]:
        """Fetch and parse merged-forward message contents."""
        data = await self.post_api("get_forwarded_messages", {"forward_id": forward_id})
        return parse_incoming_forwarded_messages(data.get("messages"))

    async def upload_private_file(
        self, user_id: int, upload: OutgoingFileUpload
    ) -> dict[str, Any]:
        """Upload a file to a private chat through Milky file API."""
        return await self.post_api(
            "upload_private_file", upload.private_payload(user_id), retry=True
        )

    async def upload_group_file(
        self, group_id: int, upload: OutgoingFileUpload
    ) -> dict[str, Any]:
        """Upload a file to a group through Milky file API."""
        return await self.post_api(
            "upload_group_file", upload.group_payload(group_id), retry=True
        )

    async def get_private_file_download_url(
        self, *, user_id: int, file_id: str, file_hash: str
    ) -> str:
        """Return the download URL for a private file."""
        data = await self.post_api(
            "get_private_file_download_url",
            {"user_id": user_id, "file_id": file_id, "file_hash": file_hash},
        )
        return str(data.get("download_url", ""))

    async def get_group_file_download_url(self, *, group_id: int, file_id: str) -> str:
        """Return the download URL for a group file."""
        data = await self.post_api(
            "get_group_file_download_url",
            {"group_id": group_id, "file_id": file_id},
        )
        return str(data.get("download_url", ""))

    async def _post_api_once(
        self, api_name: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        client = self._ensure_client()
        url = self._api_url(api_name)

        try:
            response = await client.post(url, json=payload, headers=self._headers())
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise MilkyNetworkError(
                f"Milky API {api_name} network failure: {exc}",
                api_name=api_name,
                retryable=True,
            ) from exc
        except httpx.TimeoutException as exc:
            raise MilkyNetworkError(
                f"Milky API {api_name} timed out: {exc}",
                api_name=api_name,
                retryable=False,
            ) from exc
        except httpx.RequestError as exc:
            raise MilkyNetworkError(
                f"Milky API {api_name} request failed: {exc}",
                api_name=api_name,
                retryable=False,
            ) from exc

        if response.status_code == 401:
            raise MilkyAuthError(
                "Milky access token was rejected",
                api_name=api_name,
                status_code=response.status_code,
            )
        if response.status_code != 200:
            raise MilkyHTTPStatusError(
                f"Milky API {api_name} returned HTTP {response.status_code}",
                api_name=api_name,
                status_code=response.status_code,
            )

        try:
            envelope = response.json()
        except ValueError as exc:
            raise MilkyResponseError(
                f"Milky API {api_name} returned invalid JSON",
                api_name=api_name,
            ) from exc

        if not isinstance(envelope, dict):
            raise MilkyResponseError(
                f"Milky API {api_name} response must be a JSON object",
                api_name=api_name,
            )

        status = envelope.get("status")
        retcode = envelope.get("retcode")
        data = envelope.get("data", {})
        if status != "ok" or retcode != 0:
            raise MilkyAPIError(
                str(envelope.get("message") or f"Milky API {api_name} failed"),
                api_name=api_name,
                retcode=coerce_int(retcode),
                data=envelope if isinstance(envelope, dict) else {},
            )
        if not isinstance(data, dict):
            raise MilkyResponseError(
                f"Milky API {api_name} data must be a JSON object",
                api_name=api_name,
            )
        return data

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._closed:
            raise MilkyClientClosedError("MilkyClient has been closed")
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._config.connect_timeout)
            )
            self._owns_client = True
        return self._client

    def _api_url(self, api_name: str) -> str:
        api_name = api_name.strip().lstrip("/")
        if not api_name:
            raise ValueError("api_name must not be empty")
        return f"{self._config.api_base_url}/{api_name}"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._config.access_token:
            headers["Authorization"] = f"Bearer {self._config.access_token}"
        return headers


def _serialize_message(
    message: Sequence[OutgoingSegmentPayload],
) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for segment in message:
        if isinstance(segment, dict):
            serialized.append(segment)
        else:
            serialized.append(segment.to_dict())
    return serialized
