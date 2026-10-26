"""Filesystem catalog with content IDs, sidecar metadata and bounded reads."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, Field


_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_MIME_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


class MemeMetadata(BaseModel):
    """Optional metadata in an adjacent ``image.gif.json`` file."""

    title: str = Field(default="", max_length=120)
    tags: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(
        default_factory=list, max_length=32
    )
    description: str = Field(default="", max_length=500)
    source_chat: str = Field(default="", max_length=256)
    source_user_id: str = Field(default="", max_length=256)
    source_media_id: str = Field(default="", max_length=512)
    collected_at: float = Field(default=0, ge=0)


@dataclass(frozen=True, slots=True)
class Meme:
    """One validated asset; only catalog IDs are exposed to the send tool."""

    id: str
    path: Path
    category: str
    title: str
    tags: tuple[str, ...]
    description: str
    mime_type: str
    source_chat: str = ""
    source_user_id: str = ""
    source_media_id: str = ""
    collected_at: float = 0

    def public(self) -> dict[str, Any]:
        """Return searchable metadata without disclosing filesystem paths."""
        return {
            "id": self.id,
            "category": self.category,
            "title": self.title,
            "tags": list(self.tags),
            "description": self.description,
        }

    def admin_public(self) -> dict[str, Any]:
        """Provenance is shown only in the authenticated management page."""
        return {
            **self.public(),
            "source_chat": self.source_chat,
            "source_user_id": self.source_user_id,
            "source_media_id": self.source_media_id,
            "collected_at": self.collected_at,
        }


class MemeLibrary:
    """Index only files contained in the operator-configured library root."""

    def __init__(self, root: Path, *, max_image_bytes: int, max_items: int) -> None:
        self.root = root.expanduser().resolve()
        self.max_image_bytes = max_image_bytes
        self.max_items = max_items
        self.items: dict[str, Meme] = {}
        self.skipped = 0
        self.truncated = False
        self._cache: dict[Path, tuple[tuple[int, ...], Meme]] = {}
        # Cancelling an asyncio.to_thread await does not stop its worker.
        self._io_lock = threading.RLock()

    def refresh(self) -> None:
        """Reconcile additions/removals; unchanged files reuse their content hash."""
        with self._io_lock:
            self._refresh()

    def _refresh(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        items: dict[str, Meme] = {}
        cache: dict[Path, tuple[tuple[int, ...], Meme]] = {}
        self.skipped = 0
        self.truncated = False
        for path in self._image_paths():
            if len(items) >= self.max_items:
                self.truncated = True
                break
            try:
                safe_path = self._contained(path)
                stat = safe_path.stat()
                sidecar = self._contained(Path(f"{path}.json"))
                side_stat = sidecar.stat() if sidecar.exists() else None
                signature = (
                    stat.st_mtime_ns,
                    stat.st_size,
                    side_stat.st_mtime_ns if side_stat else 0,
                    side_stat.st_size if side_stat else 0,
                )
                previous = self._cache.get(path)
                if previous is not None and previous[0] == signature:
                    meme = previous[1]
                else:
                    data, mime = self.read_image(safe_path)
                    metadata = self._read_metadata(sidecar)
                    relative = path.relative_to(self.root)
                    meme = Meme(
                        id=hashlib.sha256(data).hexdigest()[:20],
                        path=path,
                        category=relative.parent.as_posix()
                        if relative.parent != Path(".")
                        else "default",
                        title=metadata.title or path.stem[:120],
                        tags=tuple(metadata.tags),
                        description=metadata.description,
                        mime_type=mime,
                        source_chat=metadata.source_chat,
                        source_user_id=metadata.source_user_id,
                        source_media_id=metadata.source_media_id,
                        collected_at=metadata.collected_at,
                    )
                cache[path] = (signature, meme)
                items.setdefault(meme.id, meme)
            except (OSError, ValueError):
                self.skipped += 1
        self.items = items
        self._cache = cache

    def search(self, query: str, category: str, limit: int) -> list[Meme]:
        """Require every query term, ranking title and exact tag matches first."""
        terms = re.findall(r"[^\W_]+", query.casefold())
        ranked: list[tuple[int, Meme]] = []
        for meme in self.items.values():
            if category and meme.category.casefold() != category.casefold():
                continue
            title = meme.title.casefold()
            tags = {tag.casefold() for tag in meme.tags}
            haystack = " ".join(
                (title, meme.category.casefold(), *tags, meme.description.casefold())
            )
            if all(term in haystack for term in terms):
                score = sum(4 * (term in tags) + 2 * (term in title) for term in terms)
                ranked.append((score, meme))
        ranked.sort(key=lambda item: (-item[0], item[1].title, item[1].id))
        return [meme for _, meme in ranked[:limit]]

    def import_file(
        self, source: Path, *, category: str, metadata: MemeMetadata
    ) -> Meme:
        """Copy a workspace image, preserving bytes and deduplicating by content."""
        with self._io_lock:
            return self._import_file(source, category=category, metadata=metadata)

    def _import_file(
        self, source: Path, *, category: str, metadata: MemeMetadata
    ) -> Meme:
        if not re.fullmatch(r"[\w\-\u4e00-\u9fff]{1,60}", category):
            raise ValueError(
                "Category must be 1-60 letters, digits, underscores or hyphens"
            )
        data, mime = self.read_image(source)
        meme_id = hashlib.sha256(data).hexdigest()[:20]
        if meme_id in self.items:
            return self.items[meme_id]
        if len(self.items) >= self.max_items:
            raise ValueError(
                "Meme library is full; increase max_items or curate the library"
            )
        target = self._contained(
            self.root / category / (meme_id + _MIME_EXTENSIONS[mime])
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        sidecar = self._contained(Path(f"{target}.json"))
        metadata_bytes = _metadata_bytes(metadata)
        _atomic_write(target, data)
        _atomic_write(sidecar, metadata_bytes)
        self.refresh()
        return self.items[meme_id]

    def read_image(self, path: Path) -> tuple[bytes, str]:
        """Bound memory use and require a supported raster image signature."""
        if path.stat().st_size > self.max_image_bytes:
            raise ValueError("Image exceeds max_image_bytes")
        with path.open("rb") as stream:
            data = stream.read(self.max_image_bytes + 1)
        if not data or len(data) > self.max_image_bytes:
            raise ValueError("Image is empty or exceeds max_image_bytes")
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return data, "image/png"
        if data.startswith(b"\xff\xd8\xff"):
            return data, "image/jpeg"
        if data.startswith((b"GIF87a", b"GIF89a")):
            return data, "image/gif"
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            return data, "image/webp"
        raise ValueError("Only PNG, JPEG, GIF and WebP images are supported")

    def resolve(self, meme_id: str) -> Meme:
        """Recheck containment, size and content so replaced assets cannot leak."""
        return self.read_asset(meme_id)[0]

    def read_asset(self, meme_id: str) -> tuple[Meme, bytes]:
        """Read a current catalog asset with its verified original bytes."""
        meme = self.items.get(meme_id)
        if meme is None:
            raise ValueError("Unknown meme ID; search the library first")
        data, _mime = self.read_image(self._contained(meme.path))
        if hashlib.sha256(data).hexdigest()[:20] != meme_id:
            raise ValueError("Meme changed; search again after the next refresh")
        return meme, data

    def update_metadata(
        self, meme_id: str, *, title: str, tags: list[str], description: str
    ) -> Meme:
        """Edit searchable fields while retaining trusted collection provenance."""
        with self._io_lock:
            meme = self.resolve(meme_id)
            metadata = MemeMetadata(
                title=title,
                tags=tags,
                description=description,
                source_chat=meme.source_chat,
                source_user_id=meme.source_user_id,
                source_media_id=meme.source_media_id,
                collected_at=meme.collected_at,
            )
            sidecar = self._contained(Path(f"{meme.path}.json"))
            _atomic_write(sidecar, _metadata_bytes(metadata))
            self.refresh()
            return self.items[meme_id]

    def delete(self, meme_id: str) -> int:
        """Remove only matching library assets and sidecars, including duplicates."""
        with self._io_lock:
            self.resolve(meme_id)
            targets: list[tuple[Path, Path]] = []
            for path in self._image_paths():
                try:
                    safe = self._contained(path)
                    if self._content_id(path, safe) == meme_id:
                        # Verify cached candidates again before deleting them.
                        data, _mime = self.read_image(safe)
                        if hashlib.sha256(data).hexdigest()[:20] != meme_id:
                            continue
                        sidecar = self._contained(Path(f"{path}.json"))
                        targets.append((path, sidecar))
                except (OSError, ValueError):
                    continue
            for path, sidecar in targets:
                path.unlink()
                sidecar.unlink(missing_ok=True)
            self.refresh()
            return len(targets)

    def _content_id(self, path: Path, safe: Path) -> str:
        cached = self._cache.get(path)
        stat = safe.stat()
        if cached is not None and cached[0][:2] == (stat.st_mtime_ns, stat.st_size):
            return cached[1].id
        data, _mime = self.read_image(safe)
        return hashlib.sha256(data).hexdigest()[:20]

    def _contained(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError("Meme path is outside the library")
        return resolved

    def _image_paths(self) -> Iterator[Path]:
        for directory, subdirs, files in os.walk(self.root, followlinks=False):
            subdirs[:] = sorted(
                name
                for name in subdirs
                if not (Path(directory) / name).is_symlink()
                and (Path(directory) / name).resolve().is_relative_to(self.root)
            )
            for name in sorted(files):
                path = Path(directory) / name
                if path.suffix.lower() in _EXTENSIONS:
                    yield path

    @staticmethod
    def _read_metadata(path: Path) -> MemeMetadata:
        if not path.exists():
            return MemeMetadata()
        with path.open("rb") as stream:
            data = stream.read(16 * 1024 + 1)
        if len(data) > 16 * 1024:
            raise ValueError("Meme metadata is too large")
        return MemeMetadata.model_validate(json.loads(data))


def _metadata_bytes(metadata: MemeMetadata) -> bytes:
    data = metadata.model_dump_json().encode("utf-8")
    if len(data) > 16 * 1024:
        raise ValueError(
            "Meme metadata exceeds 16 KiB; shorten its tags or description"
        )
    return data


def _atomic_write(path: Path, data: bytes) -> None:
    """Publish complete files; never expose a half-written GIF or JSON sidecar."""
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
