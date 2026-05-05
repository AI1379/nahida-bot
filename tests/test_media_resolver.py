"""Tests for MediaResolver — media resolution, download, validation."""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Any

import httpx
import pytest

from nahida_bot.agent.media.cache import MediaCache
from nahida_bot.agent.media.resolver import MediaPolicy, MediaResolver
from nahida_bot.plugins.base import InboundAttachment


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    d = tmp_path / "cache"
    d.mkdir()
    return d


@pytest.fixture
def policy() -> MediaPolicy:
    return MediaPolicy(
        max_image_bytes=1024 * 1024,
        supported_mime_types=("image/jpeg", "image/png", "image/webp"),
        max_images_per_turn=4,
        cache_ttl_seconds=3600,
        cache_dir="",
    )


@pytest.fixture
def resolver(cache_dir: Path, policy: MediaPolicy) -> MediaResolver:
    cache = MediaCache(cache_dir, ttl_seconds=policy.cache_ttl_seconds)
    return MediaResolver(cache=cache, policy=policy)


class TestMediaResolver:
    async def test_resolve_from_local_path(
        self, resolver: MediaResolver, tmp_path: Path
    ) -> None:
        # Create a minimal PNG file (1x1 pixel)
        png_data = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR"
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
            b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        img_path = tmp_path / "test.png"
        img_path.write_bytes(png_data)

        attachment = InboundAttachment(
            kind="image",
            platform_id="local_1",
            path=str(img_path),
            mime_type="image/png",
        )
        result = await resolver.resolve(attachment)
        assert result.source == "path"
        assert result.base64_data != ""
        assert result.mime_type == "image/png"
        assert result.media_id == "local_1"

    async def test_resolve_description_only(self, resolver: MediaResolver) -> None:
        attachment = InboundAttachment(
            kind="image",
            platform_id="desc_1",
            alt_text="a beautiful sunset",
        )
        result = await resolver.resolve(attachment)
        assert result.source == "description_only"
        assert result.description == "a beautiful sunset"
        assert result.base64_data == ""

    async def test_resolve_nonexistent_path_falls_back(
        self, resolver: MediaResolver
    ) -> None:
        attachment = InboundAttachment(
            kind="image",
            platform_id="missing",
            path="/nonexistent/file.png",
            alt_text="fallback text",
            mime_type="image/png",
        )
        result = await resolver.resolve(attachment)
        assert result.source == "description_only"
        assert result.description == "fallback text"

    async def test_resolve_many_filters_by_kind(self, resolver: MediaResolver) -> None:
        attachments = [
            InboundAttachment(kind="image", platform_id="img_1", alt_text="photo"),
            InboundAttachment(kind="audio", platform_id="aud_1"),
            InboundAttachment(kind="image", platform_id="img_2", alt_text="pic"),
        ]
        results = await resolver.resolve_many(attachments)
        assert len(results) == 2
        assert all(r.source == "description_only" for r in results)

    async def test_resolve_many_respects_max_images(self, cache_dir: Path) -> None:
        policy = MediaPolicy(
            max_image_bytes=1024 * 1024,
            supported_mime_types=("image/jpeg", "image/png", "image/webp"),
            max_images_per_turn=1,
            cache_ttl_seconds=3600,
            cache_dir="",
        )
        cache = MediaCache(cache_dir, ttl_seconds=3600)
        resolver = MediaResolver(cache=cache, policy=policy)

        attachments = [
            InboundAttachment(kind="image", platform_id="img_1", alt_text="first"),
            InboundAttachment(kind="image", platform_id="img_2", alt_text="second"),
        ]
        results = await resolver.resolve_many(attachments)
        assert len(results) == 1
        assert results[0].media_id == "img_1"

    async def test_resolve_many_filters_unsupported_mime(
        self, resolver: MediaResolver
    ) -> None:
        attachments = [
            InboundAttachment(
                kind="image",
                platform_id="img_1",
                mime_type="image/gif",
                alt_text="gif",
            ),
            InboundAttachment(
                kind="image",
                platform_id="img_2",
                mime_type="image/png",
                alt_text="png",
            ),
        ]
        results = await resolver.resolve_many(attachments)
        assert len(results) == 1
        assert results[0].media_id == "img_2"

    def test_encode_base64(self, resolver: MediaResolver) -> None:
        result = resolver.encode_base64(b"hello")
        assert result == "aGVsbG8="

    def test_detect_mime_png(self, resolver: MediaResolver) -> None:
        assert resolver._detect_mime(b"\x89PNG\r\n\x1a\n...") == "image/png"

    def test_detect_mime_jpeg(self, resolver: MediaResolver) -> None:
        assert resolver._detect_mime(b"\xff\xd8\xff\xe0...") == "image/jpeg"

    def test_detect_mime_webp(self, resolver: MediaResolver) -> None:
        data = b"RIFF" + b"\x00" * 4 + b"WEBP"
        assert resolver._detect_mime(data) == "image/webp"

    def test_detect_mime_unknown(self, resolver: MediaResolver) -> None:
        assert resolver._detect_mime(b"\x00\x01\x02\x03") == ""

    async def test_rejects_private_url_before_download(
        self, cache_dir: Path, policy: MediaPolicy
    ) -> None:
        called = False

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal called
            called = True
            return httpx.Response(200, content=b"not reached")

        resolver = MediaResolver(
            cache=MediaCache(cache_dir, ttl_seconds=3600),
            policy=policy,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        result = await resolver.resolve(
            InboundAttachment(
                kind="image",
                platform_id="local_url",
                url="http://127.0.0.1/image.png",
                alt_text="fallback",
            )
        )

        assert result.source == "description_only"
        assert result.description == "fallback"
        assert called is False

    async def test_trusted_platform_url_allows_private_host(
        self, cache_dir: Path, policy: MediaPolicy
    ) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "image/png"},
                content=(
                    b"\x89PNG\r\n\x1a\n"
                    b"\x00\x00\x00\rIHDR"
                    b"\x00\x00\x00\x01\x00\x00\x00\x01"
                ),
            )

        resolver = MediaResolver(
            cache=MediaCache(cache_dir, ttl_seconds=3600),
            policy=policy,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        result = await resolver.resolve(
            InboundAttachment(
                kind="image",
                platform_id="trusted",
                url="http://127.0.0.1/image.png",
                metadata={"trusted_url": True},
            )
        )

        assert result.source == "url"
        assert result.base64_data

    async def test_download_enforces_stream_size_limit(
        self, cache_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        policy = MediaPolicy(max_image_bytes=4, supported_mime_types=("image/png",))

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "image/png"},
                content=b"12345",
            )

        resolver = MediaResolver(
            cache=MediaCache(cache_dir, ttl_seconds=3600),
            policy=policy,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        async def resolve_public(host: str) -> list[Any]:
            return [] if not host else [ipaddress.ip_address("93.184.216.34")]

        monkeypatch.setattr(resolver, "_resolve_host_addresses", resolve_public)

        result = await resolver.resolve(
            InboundAttachment(
                kind="image",
                platform_id="too_big",
                url="https://example.com/image.png",
                alt_text="fallback",
            )
        )

        assert result.source == "description_only"
        assert result.description == "fallback"

    async def test_cache_hit_revalidates_current_policy(self, cache_dir: Path) -> None:
        cache = MediaCache(cache_dir, ttl_seconds=3600)
        resolver = MediaResolver(
            cache=cache,
            policy=MediaPolicy(max_image_bytes=4, supported_mime_types=("image/png",)),
        )
        key = resolver.cache_key(
            InboundAttachment(
                kind="image", platform_id="img", url="https://example.com/img.png"
            )
        )
        await cache.put(key, b"12345", suffix=".png")

        result = await resolver.resolve(
            InboundAttachment(
                kind="image",
                platform_id="img",
                url="https://example.com/img.png",
                mime_type="image/png",
                alt_text="fallback",
            )
        )

        assert result.source == "description_only"
        assert await cache.get(key) is None
