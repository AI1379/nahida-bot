"""Tests for MediaCache — disk-based media cache with TTL."""

from __future__ import annotations

from pathlib import Path

import pytest

from nahida_bot.agent.media.cache import MediaCache


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    d = tmp_path / "media_cache"
    d.mkdir()
    return d


class TestMediaCache:
    async def test_put_and_get(self, cache_dir: Path) -> None:
        cache = MediaCache(cache_dir, ttl_seconds=3600)
        await cache.put("test_key", b"hello world", suffix=".jpg")
        result = await cache.get("test_key")
        assert result is not None
        assert Path(result).read_bytes() == b"hello world"

    async def test_get_missing_returns_none(self, cache_dir: Path) -> None:
        cache = MediaCache(cache_dir, ttl_seconds=3600)
        result = await cache.get("nonexistent")
        assert result is None

    async def test_ttl_expiry(self, cache_dir: Path) -> None:
        cache = MediaCache(cache_dir, ttl_seconds=0)
        await cache.put("expiring", b"data", suffix=".png")
        # TTL is 0, so it should expire immediately
        result = await cache.get("expiring")
        assert result is None

    async def test_cleanup_removes_expired(self, cache_dir: Path) -> None:
        cache = MediaCache(cache_dir, ttl_seconds=0)
        await cache.put("old", b"old_data", suffix=".jpg")
        removed = await cache.cleanup_expired()
        assert removed == 1
        # Files should be gone
        assert await cache.get("old") is None

    async def test_invalidate(self, cache_dir: Path) -> None:
        cache = MediaCache(cache_dir, ttl_seconds=3600)
        await cache.put("to_remove", b"data", suffix=".jpg")
        await cache.invalidate("to_remove")
        assert await cache.get("to_remove") is None

    async def test_ensure_dir_creates_directory(self, tmp_path: Path) -> None:
        new_dir = tmp_path / "nested" / "cache"
        cache = MediaCache(new_dir, ttl_seconds=3600)
        await cache.ensure_dir()
        assert new_dir.exists()

    async def test_put_overwrites_existing(self, cache_dir: Path) -> None:
        cache = MediaCache(cache_dir, ttl_seconds=3600)
        await cache.put("key", b"v1", suffix=".jpg")
        await cache.put("key", b"v2", suffix=".jpg")
        result = await cache.get("key")
        assert result is not None
        assert Path(result).read_bytes() == b"v2"

    async def test_get_missing_metadata_invalidates_entry(
        self, cache_dir: Path
    ) -> None:
        cache = MediaCache(cache_dir, ttl_seconds=3600)
        path = await cache.put("key", b"data", suffix=".jpg")
        cache._meta_path("key").unlink()

        assert await cache.get("key") is None
        assert not Path(path).exists()

    async def test_cleanup_removes_orphan_entry(self, cache_dir: Path) -> None:
        cache = MediaCache(cache_dir, ttl_seconds=3600)
        path = await cache.put("key", b"data", suffix=".jpg")
        cache._meta_path("key").unlink()

        removed = await cache.cleanup_expired()

        assert removed == 1
        assert not Path(path).exists()
