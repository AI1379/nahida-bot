"""Unified media storage and single-flight download orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from nahida_bot.agent.media.cache import CachedEntry, MediaCache


@dataclass(frozen=True, slots=True)
class MediaPayload:
    """Validated bytes and metadata ready to be committed to the cache."""

    data: bytes
    suffix: str = ""
    mime_type: str = ""
    file_name: str = ""
    file_size: int = 0


@dataclass(slots=True)
class _LockState:
    lock: asyncio.Lock
    users: int = 0


class MediaStore:
    """Coordinate one shared :class:`MediaCache` for all media consumers.

    ``get_or_create`` holds a per-key lock across cache lookup, download, and
    commit. This prevents concurrent channel events and resolver requests from
    downloading the same resource multiple times or racing on metadata.
    """

    def __init__(self, cache: MediaCache) -> None:
        self._cache = cache
        self._lock_guard = asyncio.Lock()
        self._locks: dict[str, _LockState] = {}

    @property
    def ttl_seconds(self) -> int:
        """Configured cache TTL used by the application cleanup scheduler."""
        return self._cache.ttl_seconds

    async def get_entry(self, cache_key: str) -> CachedEntry | None:
        """Return one valid cached entry, if present."""
        return await self._cache.get_entry(cache_key)

    async def get_or_create(
        self,
        cache_key: str,
        loader: Callable[[], Awaitable[MediaPayload]],
    ) -> CachedEntry:
        """Return a hit or run *loader* once and atomically cache its result."""
        async with self._key_lock(cache_key):
            existing = await self._cache.get_entry(cache_key)
            if existing is not None:
                return existing

            payload = await loader()
            file_size = payload.file_size or len(payload.data)
            path = await self._cache.put(
                cache_key,
                payload.data,
                suffix=payload.suffix,
                mime_type=payload.mime_type,
                file_name=payload.file_name,
                file_size=file_size,
            )
            return CachedEntry(
                path=path,
                mime_type=payload.mime_type,
                file_name=payload.file_name,
                file_size=file_size,
            )

    async def invalidate(self, cache_key: str) -> None:
        """Remove one entry from the shared cache."""
        async with self._key_lock(cache_key):
            await self._cache.invalidate(cache_key)

    async def cleanup_expired(self) -> int:
        """Remove expired entries from the shared cache."""
        return await self._cache.cleanup_expired()

    @asynccontextmanager
    async def _key_lock(self, cache_key: str) -> AsyncIterator[None]:
        """Acquire a reference-counted per-key lock without leaking keys."""
        async with self._lock_guard:
            state = self._locks.get(cache_key)
            if state is None:
                state = _LockState(asyncio.Lock())
                self._locks[cache_key] = state
            state.users += 1

        await state.lock.acquire()
        try:
            yield
        finally:
            state.lock.release()
            async with self._lock_guard:
                state.users -= 1
                if state.users == 0 and self._locks.get(cache_key) is state:
                    del self._locks[cache_key]
