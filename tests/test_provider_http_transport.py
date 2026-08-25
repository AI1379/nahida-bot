"""Regression tests for the shared provider HTTP transport contract."""

from __future__ import annotations

import httpx
import pytest

from nahida_bot.agent.providers._http_transport import HttpProviderTransportMixin
from nahida_bot.agent.providers.errors import (
    ProviderAuthError,
    ProviderBadResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderTransportError,
)


class _Transport(HttpProviderTransportMixin):
    name = "test-provider"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (401, ProviderAuthError),
        (403, ProviderAuthError),
        (429, ProviderRateLimitError),
        (500, ProviderTransportError),
        (400, ProviderBadResponseError),
    ],
)
def test_buffered_status_mapping_includes_response_context(
    status_code: int,
    error_type: type[Exception],
) -> None:
    transport = _Transport()
    response = httpx.Response(status_code, text="upstream detail")

    expected_detail = None if status_code == 429 else "upstream detail"
    with pytest.raises(error_type, match=expected_detail):
        transport._raise_for_status(response)


@pytest.mark.asyncio
async def test_stream_status_mapping_reads_response_body() -> None:
    transport = _Transport()
    response = httpx.Response(401, content=b"stream auth detail")

    with pytest.raises(ProviderAuthError, match="stream auth detail"):
        await transport._raise_for_stream_status(response)


@pytest.mark.asyncio
async def test_post_json_normalizes_timeout() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow upstream", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = _Transport(client)
    try:
        with pytest.raises(ProviderTimeoutError):
            await transport._post_json(
                endpoint="https://provider.test/chat",
                payload={},
                headers={},
                timeout=1,
            )
    finally:
        await transport.close()
    assert transport._client is None
