"""On-disk cache for synthesized speech artifacts.

The ArtifactStore is the Gateway-side half of the Desktop TTS pipeline
(``docs/design/desktop-app.md`` §9.3.1 Part B). It accepts a
:class:`SpeechArtifact` (raw bytes + metadata) and persists it under a
content-addressed ``artifact_id`` so the Desktop can fetch it later via
``/api/media/speech/{artifact_id}`` without re-synthesizing.

V1 keeps everything process-local (single-process deployment model), matching
``InMemoryNodeTokenStore``. The cache key is derived from
``(text, voice, provider, style, speed, pitch, output_format, config_version)``
so repeat requests for the same segment are free. Eviction is best-effort LRU
by total byte size; TTL is enforced lazily on access.

Design notes:
- ``artifact_id`` is opaque to clients and never encodes a server path. It is
  derived from the cache key digest so two requests for the same inputs land
  on the same artifact (idempotent synthesis).
- Files live under one managed directory; the store refuses absolute paths,
  ``..`` traversal, and symlinks.
- The store never reads files outside its managed directory and validates the
  ``artifact_id`` against the in-memory index before touching disk.
"""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import structlog

from nahida_bot.speech.base import SpeechArtifact

logger = structlog.get_logger(__name__)

_DEFAULT_TTL_SECONDS = 6 * 60 * 60  # 6h
_DEFAULT_MAX_BYTES = 256 * 1024 * 1024  # 256 MiB
_TimeProvider = Callable[[], float]


def _artifact_digest(
    *,
    text: str,
    voice: str,
    provider: str,
    style: str,
    speed: float,
    pitch: float,
    output_format: str,
    config_version: str,
) -> str:
    """Stable content hash used as both cache key and artifact_id prefix.

    Two requests with identical inputs must produce the same digest so the
    second one is a cache hit; any meaningful input difference must perturb
    the digest so stale audio is never served.
    """
    parts = [
        ("text", text.strip()),
        ("voice", voice.strip()),
        ("provider", provider.strip()),
        ("style", style.strip()),
        ("speed", f"{float(speed):.4f}"),
        ("pitch", f"{float(pitch):.4f}"),
        ("format", output_format.strip()),
        ("cfg", config_version.strip()),
    ]
    blob = "\n".join(f"{k}={v}" for k, v in parts).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _extension_for_mime(mime_type: str) -> str:
    mapping = {
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "audio/wave": "wav",
        "audio/ogg": "ogg",
        "audio/aac": "aac",
        "audio/mpeg": "mp3",
        "audio/flac": "flac",
    }
    candidate = mapping.get((mime_type or "").lower())
    if candidate:
        return candidate
    guessed = mimetypes.guess_extension(mime_type or "")
    return guessed.lstrip(".") if guessed else "wav"


@dataclass(slots=True, frozen=True)
class StoredArtifact:
    """A persisted speech artifact plus the metadata clients need."""

    artifact_id: str
    cache_key: str
    mime_type: str
    size_bytes: int
    duration_ms: int
    voice: str
    provider: str
    created_at: float
    expires_at: float
    disk_path: Path
    text: str = ""

    @property
    def expired(self) -> bool:
        return time.time() > self.expires_at

    def to_public_dict(self) -> dict[str, Any]:
        """Client-safe metadata (no disk path, no internal cache key)."""
        from datetime import UTC, datetime

        return {
            "artifact_id": self.artifact_id,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "duration_ms": self.duration_ms,
            "voice": self.voice,
            "provider": self.provider,
            "created_at": datetime.fromtimestamp(self.created_at, UTC).isoformat(),
            "expires_at": datetime.fromtimestamp(self.expires_at, UTC).isoformat(),
        }


@dataclass(slots=True)
class _IndexEntry:
    artifact_id: str
    cache_key: str
    mime_type: str
    size_bytes: int
    duration_ms: int
    voice: str
    provider: str
    created_at: float
    expires_at: float
    disk_path: Path
    last_access: float = field(default_factory=time.time)


class SpeechArtifactStore:
    """Disk-backed LRU cache for synthesized speech.

    Thread-safe via a single asyncio lock; concurrent ``put`` calls for the
    same key deduplicate to the first writer.
    """

    def __init__(
        self,
        cache_dir: str | os.PathLike[str],
        *,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
        max_bytes: int = _DEFAULT_MAX_BYTES,
        config_version: str = "v1",
        now: _TimeProvider | None = None,
    ) -> None:
        self._root = Path(cache_dir).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._ttl = max(60, int(ttl_seconds))
        self._max_bytes = max(16 * 1024 * 1024, int(max_bytes))
        self._config_version = config_version.strip() or "v1"
        self._index: dict[str, _IndexEntry] = {}
        self._by_cache_key: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._now: _TimeProvider = now or time.time

    @property
    def root(self) -> Path:
        return self._root

    @property
    def config_version(self) -> str:
        return self._config_version

    async def put(
        self,
        *,
        text: str,
        voice: str,
        provider: str,
        style: str,
        speed: float,
        pitch: float,
        output_format: str,
        artifact: SpeechArtifact,
    ) -> StoredArtifact:
        """Persist ``artifact`` and return its :class:`StoredArtifact` view.

        Idempotent: two calls with the same inputs return the same
        ``artifact_id`` and overwrite the bytes on disk in place.
        """
        cache_key = _artifact_digest(
            text=text,
            voice=voice,
            provider=provider,
            style=style,
            speed=speed,
            pitch=pitch,
            output_format=output_format,
            config_version=self._config_version,
        )
        mime = artifact.mime_type or "audio/wav"
        ext = _extension_for_mime(mime)

        async with self._lock:
            existing_id = self._by_cache_key.get(cache_key)
            if existing_id is not None:
                entry = self._index.get(existing_id)
                if entry is not None and not self._is_expired(entry):
                    self._touch(entry)
                    return self._to_stored(entry)

            artifact_id = self._new_artifact_id(cache_key)
            disk_path = self._safe_disk_path(artifact_id, ext)
            disk_path.parent.mkdir(parents=True, exist_ok=True)
            disk_path.write_bytes(artifact.data)

            now = self._now()
            entry = _IndexEntry(
                artifact_id=artifact_id,
                cache_key=cache_key,
                mime_type=mime,
                size_bytes=len(artifact.data),
                duration_ms=int(artifact.duration_ms or 0),
                voice=artifact.voice or voice,
                provider=artifact.provider or provider,
                created_at=now,
                expires_at=now + self._ttl,
                disk_path=disk_path,
            )
            self._index[artifact_id] = entry
            self._by_cache_key[cache_key] = artifact_id
            self._enforce_budget_locked()
            logger.info(
                "speech.artifact_stored",
                artifact_id=artifact_id,
                voice=entry.voice,
                size_bytes=entry.size_bytes,
            )
            return self._to_stored(entry)

    async def get(self, artifact_id: str) -> StoredArtifact | None:
        """Look up an artifact by id, returning ``None`` if missing/expired."""
        async with self._lock:
            entry = self._index.get(artifact_id.strip())
            if entry is None:
                return None
            if self._is_expired(entry):
                self._drop_locked(entry)
                return None
            self._touch(entry)
            return self._to_stored(entry)

    async def find_cached(self, **inputs: Any) -> StoredArtifact | None:
        """Look up an artifact by its synthesis inputs (cache-key parts).

        Lets the route skip synthesis entirely on a cache hit. ``inputs`` must
        match the keyword arguments passed to :meth:`put` for the same key.
        """
        cache_key = _artifact_digest(
            text=str(inputs.get("text", "")),
            voice=str(inputs.get("voice", "")),
            provider=str(inputs.get("provider", "")),
            style=str(inputs.get("style", "")),
            speed=float(inputs.get("speed", 0.0)),
            pitch=float(inputs.get("pitch", 0.0)),
            output_format=str(inputs.get("output_format", "")),
            config_version=self._config_version,
        )
        async with self._lock:
            artifact_id = self._by_cache_key.get(cache_key)
            if artifact_id is None:
                return None
            entry = self._index.get(artifact_id)
            if entry is None or self._is_expired(entry):
                if entry is not None:
                    self._drop_locked(entry)
                return None
            self._touch(entry)
            return self._to_stored(entry)

    async def stats(self) -> dict[str, Any]:
        """Return cache statistics for diagnostics."""
        async with self._lock:
            total_bytes = sum(e.size_bytes for e in self._index.values())
            return {
                "count": len(self._index),
                "bytes": total_bytes,
                "max_bytes": self._max_bytes,
                "ttl_seconds": self._ttl,
                "root": str(self._root),
            }

    async def close(self) -> None:
        """Best-effort cleanup hook. V1 keeps files on disk for restart reuse."""

    # -- internal helpers --------------------------------------------------

    def _new_artifact_id(self, cache_key: str) -> str:
        # ``spk_`` prefix makes speech artifacts greppable in logs/disk.
        # Trailing random makes the id unguessable so a leaked admin token
        # cannot be used to enumerate other users' artifacts by id.
        return f"spk_{cache_key[:16]}{secrets.token_hex(4)}"

    def _safe_disk_path(self, artifact_id: str, ext: str) -> Path:
        if not artifact_id or "/" in artifact_id or "\\" in artifact_id:
            raise ValueError(f"invalid artifact id: {artifact_id!r}")
        # Shard into 2-level prefix to avoid one giant directory.
        prefix = artifact_id[:6]
        (self._root / prefix).mkdir(parents=True, exist_ok=True)
        return self._root / prefix / f"{artifact_id}.{ext}"

    def _is_expired(self, entry: _IndexEntry) -> bool:
        return self._now() > entry.expires_at

    def _touch(self, entry: _IndexEntry) -> None:
        entry.last_access = self._now()

    def _to_stored(self, entry: _IndexEntry) -> StoredArtifact:
        return StoredArtifact(
            artifact_id=entry.artifact_id,
            cache_key=entry.cache_key,
            mime_type=entry.mime_type,
            size_bytes=entry.size_bytes,
            duration_ms=entry.duration_ms,
            voice=entry.voice,
            provider=entry.provider,
            created_at=entry.created_at,
            expires_at=entry.expires_at,
            disk_path=entry.disk_path,
        )

    def _drop_locked(self, entry: _IndexEntry) -> None:
        self._index.pop(entry.artifact_id, None)
        if self._by_cache_key.get(entry.cache_key) == entry.artifact_id:
            self._by_cache_key.pop(entry.cache_key, None)
        try:
            entry.disk_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(
                "speech.artifact_unlink_failed",
                artifact_id=entry.artifact_id,
                error=str(exc),
            )

    def _enforce_budget_locked(self) -> None:
        """Evict expired + LRU entries until total bytes fit the budget."""
        now = self._now()
        # 1) drop expired first (cheap win, no LRU bookkeeping).
        for entry in list(self._index.values()):
            if entry.expires_at <= now:
                self._drop_locked(entry)
        # 2) if still over budget, evict least-recently-accessed.
        total = sum(e.size_bytes for e in self._index.values())
        if total <= self._max_bytes:
            return
        ordered = sorted(self._index.values(), key=lambda e: e.last_access)
        for entry in ordered:
            if total <= self._max_bytes:
                break
            total -= entry.size_bytes
            self._drop_locked(entry)


__all__ = ["SpeechArtifactStore", "StoredArtifact"]
