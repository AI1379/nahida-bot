"""Tests for redirect-safe builtin web fetching."""

from __future__ import annotations

import httpx
import pytest

from nahida_bot.plugins.builtin.tools.web_fetch import WebFetchTools


PUBLIC_IP = "93.184.216.34"


@pytest.mark.asyncio
async def test_fetch_revalidates_redirect_destination() -> None:
    requested_hosts: list[str] = []

    def resolver(hostname: str) -> tuple[str, ...]:
        return ("127.0.0.1",) if hostname == "internal.example" else (PUBLIC_IP,)

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        return httpx.Response(
            302,
            headers={"location": "http://internal.example/secret"},
            request=request,
        )

    tools = WebFetchTools(
        resolver=resolver,
        transport=httpx.MockTransport(handler),
    )

    result = await tools.fetch("https://public.example/start")

    assert "SSRF protection" in result
    assert requested_hosts == ["public.example"]


@pytest.mark.asyncio
async def test_fetch_rejects_hostname_with_any_private_address() -> None:
    tools = WebFetchTools(
        resolver=lambda _hostname: (PUBLIC_IP, "10.0.0.8"),
        transport=httpx.MockTransport(lambda _request: httpx.Response(200)),
    )

    result = await tools.fetch("https://mixed.example/")

    assert "10.0.0.8" in result
    assert "SSRF protection" in result


@pytest.mark.asyncio
async def test_fetch_returns_and_truncates_public_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="abcdefgh", request=request)

    tools = WebFetchTools(
        resolver=lambda _hostname: (PUBLIC_IP,),
        transport=httpx.MockTransport(handler),
    )

    result = await tools.fetch("https://public.example/data", max_length=4)

    assert result == "abcd\n... (content truncated)"
