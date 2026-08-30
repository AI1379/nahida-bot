"""Shared HTTP transport behavior for network-backed providers."""

from __future__ import annotations

from collections.abc import Awaitable, Mapping
from typing import TypeVar

import httpx

from nahida_bot.agent.providers.errors import (
    ProviderAuthError,
    ProviderBadResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderTransportError,
)

_T = TypeVar("_T")


class HttpProviderTransportMixin:
    """Own the shared HTTP client lifecycle and status-code mapping."""

    __slots__ = ()

    _client: httpx.AsyncClient | None
    name: str

    def _ensure_client(self) -> httpx.AsyncClient:
        """Return the shared HTTP client, creating it if needed."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient()
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _await_transport(self, request: Awaitable[_T]) -> _T:
        """Await one HTTP operation and normalize transport failures."""
        try:
            return await request
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError() from exc
        except httpx.HTTPError as exc:
            raise ProviderTransportError(
                f"HTTP transport error communicating with {self.name}"
            ) from exc

    async def _post_json(
        self,
        *,
        endpoint: str,
        payload: dict[str, object],
        headers: dict[str, str],
        timeout: float,
        invalid_json_message: str = "Provider returned non-JSON body",
    ) -> tuple[dict[str, object], int]:
        """POST a JSON payload and return a normalized JSON object plus status."""
        response = await self._await_transport(
            self._ensure_client().post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=timeout,
            )
        )
        self._observe_response_headers(response.headers, request_headers=headers)
        self._raise_for_status(response)
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderBadResponseError(invalid_json_message) from exc
        if not isinstance(body, dict):
            raise ProviderBadResponseError(invalid_json_message)
        return body, response.status_code

    def _observe_response_headers(
        self,
        response_headers: Mapping[str, str],
        *,
        request_headers: Mapping[str, str],
    ) -> None:
        """Allow providers to retain transport-specific response state."""
        del response_headers
        del request_headers

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Map a buffered HTTP error response to the provider error contract."""
        if response.status_code in (401, 403):
            body_hint = response.text[:200] if response.text else ""
            raise ProviderAuthError(
                "Provider auth rejected request with status "
                f"{response.status_code} — {body_hint}",
                status_code=response.status_code,
            )
        if response.status_code == 429:
            raise ProviderRateLimitError()
        if response.status_code >= 500:
            body_hint = response.text[:200] if response.text else ""
            raise ProviderTransportError(
                f"Provider server error: status {response.status_code} — {body_hint}"
            )
        if response.status_code >= 400:
            body_hint = response.text[:300] if response.text else ""
            raise ProviderBadResponseError(
                f"Provider rejected request: status {response.status_code} — {body_hint}"
            )

    async def _raise_for_stream_status(self, response: httpx.Response) -> None:
        """Map an unbuffered streaming response to the provider error contract."""
        if response.status_code < 400:
            return
        raw = await response.aread()
        body_hint = raw.decode("utf-8", errors="replace")
        if response.status_code in (401, 403):
            raise ProviderAuthError(
                "Provider auth rejected request with status "
                f"{response.status_code} — {body_hint[:200]}",
                status_code=response.status_code,
            )
        if response.status_code == 429:
            raise ProviderRateLimitError()
        if response.status_code >= 500:
            raise ProviderTransportError(
                f"Provider server error: status {response.status_code} — "
                f"{body_hint[:200]}"
            )
        raise ProviderBadResponseError(
            f"Provider rejected request: status {response.status_code} — "
            f"{body_hint[:300]}"
        )
