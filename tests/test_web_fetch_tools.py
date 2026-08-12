"""Tests for redirect-safe builtin web fetching."""

from __future__ import annotations

import httpx
import pytest

from nahida_bot.plugins.builtin.tools.web_fetch import WebFetchTools


PUBLIC_IP = "93.184.216.34"


@pytest.mark.asyncio
async def test_fetch_revalidates_redirect_destination() -> None:
    requested_hosts: list[str] = []
    host_headers: list[str] = []

    def resolver(hostname: str) -> tuple[str, ...]:
        return ("127.0.0.1",) if hostname == "internal.example" else (PUBLIC_IP,)

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        host_headers.append(request.headers["host"])
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
    assert requested_hosts == [PUBLIC_IP]
    assert host_headers == ["public.example"]


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
        assert request.url.host == PUBLIC_IP
        assert request.headers["host"] == "public.example"
        assert request.headers["connection"] == "close"
        assert request.extensions["sni_hostname"] == "public.example"
        return httpx.Response(200, text="abcdefgh", request=request)

    tools = WebFetchTools(
        resolver=lambda _hostname: (PUBLIC_IP,),
        transport=httpx.MockTransport(handler),
    )

    result = await tools.fetch("https://public.example/data", max_length=4)

    assert result == "abcd\n... (content truncated)"


@pytest.mark.asyncio
async def test_fetch_pins_connection_to_the_validated_dns_answer() -> None:
    resolver_calls = 0

    def resolver(_hostname: str) -> tuple[str, ...]:
        nonlocal resolver_calls
        resolver_calls += 1
        if resolver_calls == 1:
            return (PUBLIC_IP,)
        return ("127.0.0.1",)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == PUBLIC_IP
        return httpx.Response(200, text="safe", request=request)

    tools = WebFetchTools(
        resolver=resolver,
        transport=httpx.MockTransport(handler),
    )

    result = await tools.fetch("https://rebind.example/data")

    assert result == "safe"
    assert resolver_calls == 1


@pytest.mark.asyncio
async def test_fetch_keeps_original_host_for_relative_redirects() -> None:
    requests: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.host, request.url.path, request.headers["host"]))
        if request.url.path == "/start":
            return httpx.Response(
                302,
                headers={"location": "/next"},
                request=request,
            )
        return httpx.Response(200, text="done", request=request)

    tools = WebFetchTools(
        resolver=lambda _hostname: (PUBLIC_IP,),
        transport=httpx.MockTransport(handler),
    )

    result = await tools.fetch("https://public.example/start")

    assert result == "done"
    assert requests == [
        (PUBLIC_IP, "/start", "public.example"),
        (PUBLIC_IP, "/next", "public.example"),
    ]
