"""Media resolution: download, validate, cache, and encode media artifacts."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import socket
import struct
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
import structlog

from nahida_bot.agent.media.cache import MediaCache
from nahida_bot.plugins.base import InboundAttachment

logger = structlog.get_logger(__name__)


@dataclass(slots=True, frozen=True)
class ResolvedMedia:
    """Result of resolving an inbound media reference."""

    local_path: str = ""
    base64_data: str = ""
    mime_type: str = ""
    file_size: int = 0
    width: int = 0
    height: int = 0
    media_id: str = ""
    source: str = ""  # "url" | "path" | "cache_hit" | "description_only"
    description: str = ""


@dataclass(slots=True, frozen=True)
class MediaPolicy:
    """Configuration for media validation and caching."""

    max_image_bytes: int = 10 * 1024 * 1024
    supported_mime_types: tuple[str, ...] = ("image/jpeg", "image/png", "image/webp")
    max_images_per_turn: int = 4
    cache_ttl_seconds: int = 3600
    cache_dir: str = ""


class MediaResolver:
    """Unified entry point: resolve InboundAttachment -> ResolvedMedia.

    Resolution priority:
    1. Local file path (read from disk)
    2. Cached URL (cache hit)
    3. Remote URL (download + cache)
    4. Description only (alt_text fallback)
    """

    def __init__(
        self,
        cache: MediaCache,
        policy: MediaPolicy,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._cache = cache
        self._policy = policy
        self._client = http_client

    async def resolve(self, attachment: InboundAttachment) -> ResolvedMedia:
        """Resolve a single attachment to a ResolvedMedia."""
        media_id = attachment.platform_id

        # 1. Local file path
        if attachment.path and Path(attachment.path).exists():
            return await self._resolve_from_path(attachment)

        # 2/3. URL-based (cache or download)
        if attachment.url:
            return await self._resolve_from_url(attachment)

        # 4. Description only
        return ResolvedMedia(
            media_id=media_id,
            source="description_only",
            description=attachment.alt_text,
            mime_type=attachment.mime_type,
        )

    async def resolve_many(
        self, attachments: list[InboundAttachment]
    ) -> list[ResolvedMedia]:
        """Resolve multiple attachments, filtering to images by kind."""
        images = [a for a in attachments if a.kind == "image"]
        if self._policy.max_images_per_turn > 0:
            images = images[: self._policy.max_images_per_turn]

        results: list[ResolvedMedia] = []
        for att in images:
            if att.mime_type and att.mime_type not in self._policy.supported_mime_types:
                continue
            results.append(await self.resolve(att))
        return results

    def encode_base64(self, data: bytes) -> str:
        """Encode bytes to base64 string."""
        return base64.b64encode(data).decode("ascii")

    def cache_key(self, attachment: InboundAttachment) -> str:
        """Generate a stable cache key for an attachment."""
        raw = attachment.url or attachment.platform_id
        return hashlib.sha256(raw.encode()).hexdigest()

    # -- internal --------------------------------------------------------

    async def _resolve_from_path(self, attachment: InboundAttachment) -> ResolvedMedia:
        path = Path(attachment.path)
        try:
            data = await self._read_file(path)
        except OSError as exc:
            logger.warning(
                "media_resolver.path_read_failed",
                media_id=attachment.platform_id,
                error=str(exc),
            )
            return ResolvedMedia(
                media_id=attachment.platform_id,
                source="description_only",
                description=attachment.alt_text,
                mime_type=attachment.mime_type,
            )

        mime = attachment.mime_type or self._detect_mime(data)
        size = len(data)
        width, height = self._read_image_dimensions(data, mime)

        if not self._validate(data, mime):
            return ResolvedMedia(
                media_id=attachment.platform_id,
                source="description_only",
                description=attachment.alt_text,
                mime_type=mime,
                file_size=size,
            )

        b64 = self.encode_base64(data)
        return ResolvedMedia(
            local_path=str(path),
            base64_data=b64,
            mime_type=mime,
            file_size=size,
            width=width or attachment.width,
            height=height or attachment.height,
            media_id=attachment.platform_id,
            source="path",
        )

    async def _resolve_from_url(self, attachment: InboundAttachment) -> ResolvedMedia:
        cache_key = self.cache_key(attachment)

        # Check cache first
        cached = await self._cache.get(cache_key)
        if cached is not None:
            try:
                data = await self._read_file(Path(cached))
                mime = attachment.mime_type or self._detect_mime(data)
                size = len(data)
                if not self._validate(data, mime):
                    await self._cache.invalidate(cache_key)
                    return ResolvedMedia(
                        media_id=attachment.platform_id,
                        source="description_only",
                        description=attachment.alt_text,
                        mime_type=mime,
                        file_size=size,
                    )
                width, height = self._read_image_dimensions(data, mime)
                b64 = self.encode_base64(data)
                return ResolvedMedia(
                    local_path=cached,
                    base64_data=b64,
                    mime_type=mime,
                    file_size=size,
                    width=width or attachment.width,
                    height=height or attachment.height,
                    media_id=attachment.platform_id,
                    source="cache_hit",
                )
            except OSError:
                pass  # fall through to download

        # Download
        try:
            allow_private = bool(attachment.metadata.get("trusted_url"))
            data, mime = await self._download(
                attachment.url,
                allow_private_network=allow_private,
            )
        except (httpx.HTTPError, OSError) as exc:
            logger.warning(
                "media_resolver.download_failed",
                media_id=attachment.platform_id,
                error=str(exc),
            )
            return ResolvedMedia(
                media_id=attachment.platform_id,
                source="description_only",
                description=attachment.alt_text,
                mime_type=attachment.mime_type,
            )

        if not self._validate(data, mime):
            return ResolvedMedia(
                media_id=attachment.platform_id,
                source="description_only",
                description=attachment.alt_text,
                mime_type=mime,
            )

        # Cache the download
        suffix = self._mime_to_suffix(mime)
        cached_path = await self._cache.put(cache_key, data, suffix=suffix)

        size = len(data)
        width, height = self._read_image_dimensions(data, mime)
        b64 = self.encode_base64(data)

        return ResolvedMedia(
            local_path=cached_path,
            base64_data=b64,
            mime_type=mime,
            file_size=size,
            width=width or attachment.width,
            height=height or attachment.height,
            media_id=attachment.platform_id,
            source="url",
        )

    def _validate(self, data: bytes, mime_type: str) -> bool:
        if (
            self._policy.max_image_bytes > 0
            and len(data) > self._policy.max_image_bytes
        ):
            return False
        if mime_type and mime_type not in self._policy.supported_mime_types:
            return False
        return True

    @staticmethod
    def _detect_mime(data: bytes) -> str:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        if data[:2] == b"\xff\xd8":
            return "image/jpeg"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "image/webp"
        return ""

    @staticmethod
    def _read_image_dimensions(data: bytes, mime: str) -> tuple[int, int]:
        try:
            if mime == "image/png" and len(data) >= 24:
                w = struct.unpack(">I", data[16:20])[0]
                h = struct.unpack(">I", data[20:24])[0]
                return w, h
            if mime == "image/jpeg" and len(data) >= 20:
                # SOF0 marker scan
                idx = 2
                while idx < len(data) - 9:
                    if data[idx] != 0xFF:
                        break
                    marker = data[idx + 1]
                    if marker in (0xC0, 0xC1, 0xC2):
                        h = struct.unpack(">H", data[idx + 5 : idx + 7])[0]
                        w = struct.unpack(">H", data[idx + 7 : idx + 9])[0]
                        return w, h
                    length = struct.unpack(">H", data[idx + 2 : idx + 4])[0]
                    idx += 2 + length
        except (struct.error, IndexError):
            pass
        return 0, 0

    @staticmethod
    def _mime_to_suffix(mime: str) -> str:
        return {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
        }.get(mime, ".bin")

    async def _download(
        self, url: str, *, allow_private_network: bool = False
    ) -> tuple[bytes, str]:
        await self._ensure_url_allowed(url, allow_private_network=allow_private_network)
        client = self._client or httpx.AsyncClient(timeout=30.0)
        try:
            async with client.stream("GET", url, follow_redirects=False) as response:
                response.raise_for_status()
                data = await self._read_limited_response(response)
                mime = response.headers.get("content-type", "").split(";")[0].strip()
                if not mime:
                    mime = self._detect_mime(data)
                return data, mime
        finally:
            if self._client is None:
                await client.aclose()

    async def _read_limited_response(self, response: httpx.Response) -> bytes:
        limit = self._policy.max_image_bytes
        content_length = response.headers.get("content-length")
        if limit > 0 and content_length:
            try:
                if int(content_length) > limit:
                    raise httpx.HTTPError("media response exceeds size limit")
            except ValueError:
                pass

        data = bytearray()
        async for chunk in response.aiter_bytes():
            data.extend(chunk)
            if limit > 0 and len(data) > limit:
                raise httpx.HTTPError("media response exceeds size limit")
        return bytes(data)

    async def _ensure_url_allowed(
        self, url: str, *, allow_private_network: bool = False
    ) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise httpx.HTTPError("media URL scheme is not allowed")
        if not parsed.hostname:
            raise httpx.HTTPError("media URL host is missing")

        host = parsed.hostname.strip().lower()
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
            raise httpx.HTTPError("media URL host is local")

        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            addresses = await self._resolve_host_addresses(host)

        if not addresses:
            raise httpx.HTTPError("media URL host could not be resolved")
        if not allow_private_network and any(
            self._is_disallowed_address(addr) for addr in addresses
        ):
            raise httpx.HTTPError("media URL resolves to a private address")

    @staticmethod
    async def _resolve_host_addresses(
        host: str,
    ) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        try:
            infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
        except socket.gaierror as exc:
            raise httpx.HTTPError("media URL host could not be resolved") from exc

        addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
        for info in infos:
            sockaddr = info[4]
            if not sockaddr:
                continue
            try:
                addresses.append(ipaddress.ip_address(sockaddr[0]))
            except ValueError:
                continue
        return addresses

    @staticmethod
    def _is_disallowed_address(
        addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
    ) -> bool:
        return (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_multicast
            or addr.is_reserved
            or addr.is_unspecified
        )

    @staticmethod
    async def _read_file(path: Path) -> bytes:
        import aiofiles

        async with aiofiles.open(str(path), "rb") as f:
            return await f.read()
