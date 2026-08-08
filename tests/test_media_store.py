"""Tests for unified media-store orchestration."""

from __future__ import annotations

import asyncio
from pathlib import Path

from nahida_bot.agent.media.cache import MediaCache
from nahida_bot.agent.media.store import MediaPayload, MediaStore


async def test_get_or_create_single_flights_same_key(tmp_path: Path) -> None:
    store = MediaStore(MediaCache(tmp_path / "media_cache"))
    calls = 0

    async def loader() -> MediaPayload:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return MediaPayload(
            data=b"payload",
            suffix=".bin",
            mime_type="application/octet-stream",
            file_name="payload.bin",
        )

    entries = await asyncio.gather(
        *(store.get_or_create("same-key", loader) for _ in range(5))
    )

    assert calls == 1
    assert {entry.path for entry in entries} == {
        entries[0].path,
    }
    assert Path(entries[0].path).read_bytes() == b"payload"
    assert entries[0].file_name == "payload.bin"
    assert store._locks == {}


async def test_get_or_create_does_not_leave_lock_after_loader_failure(
    tmp_path: Path,
) -> None:
    store = MediaStore(MediaCache(tmp_path / "media_cache"))

    async def loader() -> MediaPayload:
        raise RuntimeError("download failed")

    try:
        await store.get_or_create("failed-key", loader)
    except RuntimeError:
        pass
    else:
        raise AssertionError("loader failure was swallowed")

    assert store._locks == {}
