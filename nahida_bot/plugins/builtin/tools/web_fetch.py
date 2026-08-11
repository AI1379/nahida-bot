"""HTTP fetch tool with redirect-aware SSRF protection."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
import structlog
from markdownify import markdownify as md
from readability import Document

from nahida_bot.plugins.tooling import PluginToolDefinition


_logger = structlog.get_logger(__name__)
_TIMEOUT_SECONDS = 30
_MAX_BODY_BYTES = 5 * 1024 * 1024
_MAX_REDIRECTS = 5
_USER_AGENT = "NahidaBot/0.1 (web_fetch tool)"

_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "description": "The URL to fetch (http or https).",
        },
        "max_length": {
            "type": "integer",
            "description": "Maximum content length in characters (default 10000).",
        },
    },
    "required": ["url"],
    "additionalProperties": False,
}

HostResolver = Callable[[str], tuple[str, ...]]


class WebFetchTools:
    """Define and execute bounded public-network HTTP fetches."""

    def __init__(
        self,
        *,
        resolver: HostResolver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._resolver = resolver or self.resolve_host
        self._transport = transport

    def definitions(self) -> tuple[PluginToolDefinition, ...]:
        """Return the web fetch tool exposed to the model."""
        return (
            PluginToolDefinition(
                name="web_fetch",
                description="Fetch a web page and return its main content as Markdown.",
                parameters=_PARAMETERS,
                handler=self.fetch,
            ),
        )

    @staticmethod
    def is_disallowed_ip(ip_text: str) -> bool:
        """Return whether an address is unsafe for an outbound fetch."""
        try:
            address = ipaddress.ip_address(ip_text)
        except ValueError:
            return True
        return any(
            (
                address.is_private,
                address.is_loopback,
                address.is_link_local,
                address.is_multicast,
                address.is_reserved,
                address.is_unspecified,
            )
        )

    @staticmethod
    def resolve_host(hostname: str) -> tuple[str, ...]:
        """Resolve all unique addresses for a hostname."""
        try:
            results = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC)
        except (socket.gaierror, OSError):
            return ()
        return tuple(
            dict.fromkeys(
                address
                for _family, _type, _proto, _canon, sockaddr in results
                if isinstance((address := sockaddr[0]), str)
            )
        )

    @staticmethod
    def html_to_markdown(html_content: str) -> str:
        """Extract the readable portion of HTML and convert it to Markdown."""
        try:
            summary_html = Document(html_content).summary()
            return md(summary_html, strip=["img", "script", "style"])
        except Exception:
            return md(html_content, strip=["img", "script", "style"])

    def _validate_url(self, url: str) -> str | None:
        parsed = urlparse(url)
        if parsed.scheme.lower() not in {"http", "https"}:
            return f"Error: URL must start with http:// or https://. Got: {url}"
        if not parsed.hostname:
            return f"Error: Could not parse hostname from URL: {url}"

        addresses = self._resolver(parsed.hostname)
        if not addresses:
            return f"Error: Could not resolve hostname: {parsed.hostname}"
        blocked = next((ip for ip in addresses if self.is_disallowed_ip(ip)), None)
        if blocked is not None:
            return (
                f"Error: URL resolves to private/internal IP {blocked}. "
                "Access denied (SSRF protection)."
            )
        return None

    async def _request_public_response(
        self,
        client: httpx.AsyncClient,
        url: str,
    ) -> httpx.Response | str:
        current_url = url
        for redirect_count in range(_MAX_REDIRECTS + 1):
            validation_error = self._validate_url(current_url)
            if validation_error is not None:
                return validation_error

            response = await client.get(
                current_url,
                headers={"User-Agent": _USER_AGENT},
            )
            if not response.is_redirect:
                response.raise_for_status()
                return response

            location = response.headers.get("location")
            if not location:
                response.raise_for_status()
            if redirect_count >= _MAX_REDIRECTS:
                return f"Request failed: exceeded {_MAX_REDIRECTS} redirects"
            current_url = urljoin(str(response.url), location)
        return "Request failed: no response received"

    def _render_response(self, response: httpx.Response, max_length: int) -> str:
        if len(response.content) > _MAX_BODY_BYTES:
            return (
                "Error: Response body exceeds "
                f"{_MAX_BODY_BYTES // 1024 // 1024}MB limit."
            )

        content_type = response.headers.get("content-type", "")
        result = (
            self.html_to_markdown(response.text)
            if "text/html" in content_type
            else response.text
        )
        if len(result) > max_length:
            return result[:max_length] + "\n... (content truncated)"
        return result

    async def fetch(self, url: str, max_length: int = 10000) -> str:
        """Fetch one public URL, validating every redirect destination."""
        _logger.debug("tool.web_fetch", url=url, max_length=max_length)

        try:
            async with httpx.AsyncClient(
                follow_redirects=False,
                timeout=httpx.Timeout(_TIMEOUT_SECONDS),
                transport=self._transport,
            ) as client:
                response = await self._request_public_response(client, url)
                if isinstance(response, str):
                    return response
                return self._render_response(response, max_length)
        except httpx.HTTPStatusError as exc:
            return (
                f"HTTP error {exc.response.status_code}: {exc.response.reason_phrase}"
            )
        except httpx.RequestError as exc:
            return f"Request failed: {exc}"
        except Exception as exc:
            _logger.exception("tool.web_fetch.error", url=url)
            return f"Failed to fetch URL: {exc}"
