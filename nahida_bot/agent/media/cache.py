"""On-disk media cache with TTL-based expiry."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import aiofiles
import aiofiles.os


class MediaCache:
    """Disk-backed cache for downloaded media artifacts.

    Each entry is stored as a file named by the SHA-256 hex digest of its
    cache key, with a companion ``.<hash>_meta.json`` storing the original
    URL and cached timestamp for TTL tracking.
    """

    def __init__(self, cache_dir: str | Path, *, ttl_seconds: int = 3600) -> None:
        self._dir = Path(cache_dir)
        self._ttl = ttl_seconds

    async def ensure_dir(self) -> None:
        """Create cache directory if it does not exist."""
        await aiofiles.os.makedirs(str(self._dir), exist_ok=True)

    async def get(self, cache_key: str) -> str | None:
        """Return cached file path if present and not expired, else ``None``."""
        hashed = self._hash_key(cache_key)
        entry = self._find_entry(hashed)
        if entry is None:
            return None

        meta = await self._read_meta(cache_key)
        if meta is None:
            await self._remove_entry(cache_key)
            return None

        cached_at = meta.get("cached_at", 0.0)
        if time.monotonic() - cached_at >= self._ttl:
            await self._remove_entry(cache_key)
            return None

        return str(entry)

    async def put(self, cache_key: str, data: bytes, suffix: str = "") -> str:
        """Write data to cache and return the file path."""
        await self.ensure_dir()
        entry = self._entry_path(cache_key, suffix=suffix)
        async with aiofiles.open(str(entry), "wb") as f:
            await f.write(data)

        await self._write_meta(cache_key, {"cached_at": time.monotonic()})
        return str(entry)

    async def invalidate(self, cache_key: str) -> None:
        """Remove a cached entry and its metadata."""
        await self._remove_entry(cache_key)

    async def cleanup_expired(self) -> int:
        """Remove all expired entries. Returns count of removed items."""
        if not self._dir.exists():
            return 0

        removed = 0
        now = time.monotonic()
        for meta_file in self._dir.glob("*_meta.json"):
            try:
                async with aiofiles.open(str(meta_file), "r", encoding="utf-8") as f:
                    raw = await f.read()
                meta = json.loads(raw)
                cached_at = meta.get("cached_at", 0.0)
                if now - cached_at >= self._ttl:
                    stem = meta_file.stem.removesuffix("_meta")
                    await self._remove_entry_by_stem(stem)
                    removed += 1
            except (json.JSONDecodeError, OSError, ValueError):
                stem = meta_file.stem.removesuffix("_meta")
                await self._remove_entry_by_stem(stem)
                removed += 1
                continue
        for entry in self._dir.iterdir():
            if entry.name.endswith("_meta.json"):
                continue
            stem = entry.stem if entry.suffix else entry.name
            meta_file = self._dir / f"{stem}_meta.json"
            if not meta_file.exists():
                await self._remove_entry_by_stem(stem)
                removed += 1
        return removed

    # -- internal helpers ------------------------------------------------

    def _hash_key(self, cache_key: str) -> str:
        return hashlib.sha256(cache_key.encode()).hexdigest()

    def _entry_path(self, cache_key: str, *, suffix: str = "") -> Path:
        hashed = self._hash_key(cache_key)
        return self._dir / f"{hashed}{suffix}"

    def _find_entry(self, hashed: str) -> Path | None:
        """Find a cache entry file by hash prefix, skipping meta files."""
        if not self._dir.exists():
            return None
        for path in self._dir.iterdir():
            name = path.name
            if name.startswith(hashed) and not name.endswith("_meta.json"):
                return path
        return None

    def _meta_path(self, cache_key: str) -> Path:
        hashed = self._hash_key(cache_key)
        return self._dir / f"{hashed}_meta.json"

    async def _read_meta(self, cache_key: str) -> dict | None:
        meta_path = self._meta_path(cache_key)
        if not meta_path.exists():
            return None
        try:
            async with aiofiles.open(str(meta_path), "r", encoding="utf-8") as f:
                raw = await f.read()
            return json.loads(raw)
        except (json.JSONDecodeError, OSError):
            return None

    async def _write_meta(self, cache_key: str, meta: dict) -> None:
        meta_path = self._meta_path(cache_key)
        async with aiofiles.open(str(meta_path), "w", encoding="utf-8") as f:
            await f.write(json.dumps(meta))

    async def _remove_entry(self, cache_key: str) -> None:
        hashed = self._hash_key(cache_key)
        await self._remove_entry_by_stem(hashed)

    async def _remove_entry_by_stem(self, hashed_stem: str) -> None:
        for path in self._dir.iterdir():
            name = path.name
            if name.startswith(hashed_stem):
                try:
                    await aiofiles.os.remove(str(path))
                except OSError:
                    pass
