"""Tests for MediaCache — disk-based media cache with TTL."""

from __future__ import annotations

import os
import time
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

    async def test_get_missing_returns_none_when_directory_is_absent(
        self, tmp_path: Path
    ) -> None:
        cache = MediaCache(tmp_path / "missing", ttl_seconds=3600)
        assert await cache.get("nonexistent") is None

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
        old_time = time.time() - 3600
        os.utime(path, (old_time, old_time))

        assert await cache.get("key") is None
        assert await cache.cleanup_expired() == 1
        assert not Path(path).exists()

    async def test_cleanup_removes_orphan_entry(self, cache_dir: Path) -> None:
        cache = MediaCache(cache_dir, ttl_seconds=3600)
        path = await cache.put("key", b"data", suffix=".jpg")
        cache._meta_path("key").unlink()
        old_time = time.time() - 3600
        os.utime(path, (old_time, old_time))

        removed = await cache.cleanup_expired()

        assert removed == 1
        assert not Path(path).exists()

    async def test_put_records_optional_metadata(self, cache_dir: Path) -> None:
        cache = MediaCache(cache_dir, ttl_seconds=3600)
        await cache.put(
            "k",
            b"data",
            suffix=".png",
            mime_type="image/png",
            file_name="photo.png",
            file_size=4,
        )
        entry = await cache.get_entry("k")
        assert entry is not None
        assert Path(entry.path).read_bytes() == b"data"
        assert entry.mime_type == "image/png"
        assert entry.file_name == "photo.png"
        assert entry.file_size == 4

    async def test_get_entry_returns_none_when_missing(self, cache_dir: Path) -> None:
        cache = MediaCache(cache_dir, ttl_seconds=3600)
        assert await cache.get_entry("absent") is None

    async def test_get_entry_expires_with_ttl(self, cache_dir: Path) -> None:
        cache = MediaCache(cache_dir, ttl_seconds=0)
        await cache.put("k", b"data", suffix=".jpg", file_name="x.jpg")
        assert await cache.get_entry("k") is None

    async def test_get_entry_survives_missing_optional_meta(
        self, cache_dir: Path
    ) -> None:
        cache = MediaCache(cache_dir, ttl_seconds=3600)
        await cache.put("k", b"data", suffix=".jpg")
        entry = await cache.get_entry("k")
        assert entry is not None
        assert entry.mime_type == ""
        assert entry.file_name == ""
        assert entry.file_size == 0

    async def test_cleanup_keeps_fresh_atomic_write_temp_file(
        self, cache_dir: Path
    ) -> None:
        temp_file = cache_dir / ("a" * 64 + ".tmp")
        temp_file.write_bytes(b"in progress")
        cache = MediaCache(cache_dir, ttl_seconds=3600)

        assert await cache.cleanup_expired() == 0
        assert temp_file.exists()
