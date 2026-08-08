"""On-disk media cache with TTL-based expiry."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import aiofiles
import aiofiles.os

# Sentinel value stored in meta for entries written with the old
# ``time.monotonic()`` clock.  Such entries are considered expired on the
# next read so they are refreshed automatically.
_MONOTONIC_SENTINEL = -1.0


@dataclass(frozen=True, slots=True)
class CachedEntry:
    """A cached media file with optional provenance metadata.

    Channels and the agent resolver share one cache, so entries may carry
    the MIME type, original file name, and size recorded at download time.
    All metadata fields are optional — only the path is guaranteed.
    """

    path: str
    mime_type: str = ""
    file_name: str = ""
    file_size: int = 0


class MediaCache:
    """Disk-backed cache for downloaded media artifacts.

    Each entry is stored as a file named by the SHA-256 hex digest of its
    cache key, with a companion ``.<hash>_meta.json`` storing the cached
    timestamp, suffix, and optional caller metadata for TTL tracking.
    """

    def __init__(self, cache_dir: str | Path, *, ttl_seconds: int = 3600) -> None:
        self._dir = Path(cache_dir)
        self._ttl = ttl_seconds

    async def ensure_dir(self) -> None:
        """Create cache directory if it does not exist."""
        await aiofiles.os.makedirs(str(self._dir), exist_ok=True)

    @property
    def ttl_seconds(self) -> int:
        """Configured lifetime of cache entries in seconds."""
        return self._ttl

    async def get(self, cache_key: str) -> str | None:
        """Return cached file path if present and not expired, else ``None``."""
        hashed = self._hash_key(cache_key)
        meta = await self._read_meta(cache_key)
        if meta is None:
            return None

        cached_at = meta.get("cached_at", 0.0)
        # Entries that used the old monotonic clock are forced-expired.
        if cached_at == _MONOTONIC_SENTINEL or time.time() - cached_at >= self._ttl:
            await self._remove_entry(cache_key)
            return None

        # Reconstruct the file path from the suffix stored in meta.
        suffix = meta.get("suffix", "")
        entry = self._dir / f"{hashed}{suffix}"
        if not entry.exists():
            # File vanished out-of-band; clean up orphaned meta.
            await self._remove_entry(cache_key)
            return None

        return str(entry)

    async def get_entry(self, cache_key: str) -> CachedEntry | None:
        """Return a cached entry with metadata if present and not expired."""
        path = await self.get(cache_key)
        if path is None:
            return None
        meta = await self._read_meta(cache_key) or {}
        try:
            file_size = int(meta.get("file_size") or 0)
        except (TypeError, ValueError):
            file_size = 0
        return CachedEntry(
            path=path,
            mime_type=str(meta.get("mime_type") or ""),
            file_name=str(meta.get("file_name") or ""),
            file_size=file_size,
        )

    async def put(
        self,
        cache_key: str,
        data: bytes,
        suffix: str = "",
        *,
        mime_type: str = "",
        file_name: str = "",
        file_size: int = 0,
    ) -> str:
        """Write data to cache and return the file path.

        Optional ``mime_type`` / ``file_name`` / ``file_size`` are recorded
        in the entry metadata so callers (e.g. channel plugins) can recover
        them on a later cache hit via :meth:`get_entry`.
        """
        await self.ensure_dir()
        entry = self._entry_path(cache_key, suffix=suffix)
        await self._write_bytes_atomic(entry, data)

        meta: dict = {"cached_at": time.time(), "suffix": suffix}
        if mime_type:
            meta["mime_type"] = mime_type
        if file_name:
            meta["file_name"] = file_name
        if file_size:
            meta["file_size"] = file_size
        await self._write_meta(cache_key, meta)
        return str(entry)

    async def invalidate(self, cache_key: str) -> None:
        """Remove a cached entry and its metadata."""
        await self._remove_entry(cache_key)

    async def cleanup_expired(self) -> int:
        """Remove all expired entries. Returns count of removed items."""
        if not self._dir.exists():
            return 0

        removed = 0
        now = time.time()
        for meta_file in self._dir.glob("*_meta.json"):
            stem = meta_file.stem.removesuffix("_meta")
            try:
                async with aiofiles.open(str(meta_file), "r", encoding="utf-8") as f:
                    raw = await f.read()
                meta = json.loads(raw)
                cached_at = meta.get("cached_at", 0.0)
                if cached_at == _MONOTONIC_SENTINEL or now - cached_at >= self._ttl:
                    await self._remove_entry_by_stem(stem)
                    removed += 1
            except (json.JSONDecodeError, OSError, ValueError):
                await self._remove_entry_by_stem(stem)
                removed += 1
                continue
        for entry in self._dir.iterdir():
            if entry.name.endswith("_meta.json"):
                continue
            if entry.suffix == ".tmp":
                try:
                    if now - entry.stat().st_mtime >= max(self._ttl, 300):
                        await aiofiles.os.remove(str(entry))
                        removed += 1
                except OSError:
                    pass
                continue
            stem = entry.stem if entry.suffix else entry.name
            meta_file = self._dir / f"{stem}_meta.json"
            if not meta_file.exists():
                try:
                    if now - entry.stat().st_mtime >= max(self._ttl, 300):
                        await self._remove_entry_by_stem(stem)
                        removed += 1
                except OSError:
                    pass
        return removed

    # -- internal helpers ------------------------------------------------

    def _hash_key(self, cache_key: str) -> str:
        return hashlib.sha256(cache_key.encode()).hexdigest()

    def _entry_path(self, cache_key: str, *, suffix: str = "") -> Path:
        hashed = self._hash_key(cache_key)
        return self._dir / f"{hashed}{suffix}"

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
        await self._write_text_atomic(meta_path, json.dumps(meta))

    async def _write_bytes_atomic(self, path: Path, data: bytes) -> None:
        """Write bytes beside the target and atomically replace it."""
        fd, temp_path = tempfile.mkstemp(
            prefix=f"{path.stem}.", suffix=".tmp", dir=str(self._dir)
        )
        os.close(fd)
        try:
            async with aiofiles.open(temp_path, "wb") as f:
                await f.write(data)
            os.replace(temp_path, path)
        finally:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    async def _write_text_atomic(self, path: Path, text: str) -> None:
        """Write text beside the target and atomically replace it."""
        fd, temp_path = tempfile.mkstemp(
            prefix=f"{path.stem}.", suffix=".tmp", dir=str(self._dir)
        )
        os.close(fd)
        try:
            async with aiofiles.open(temp_path, "w", encoding="utf-8") as f:
                await f.write(text)
            os.replace(temp_path, path)
        finally:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    async def _remove_entry(self, cache_key: str) -> None:
        hashed = self._hash_key(cache_key)
        await self._remove_entry_by_stem(hashed)

    async def _remove_entry_by_stem(self, hashed_stem: str) -> None:
        if not self._dir.exists():
            return
        for path in self._dir.iterdir():
            name = path.name
            if name.startswith(hashed_stem):
                try:
                    await aiofiles.os.remove(str(path))
                except OSError:
                    pass
