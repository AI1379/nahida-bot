"""Admin operations owned by the meme plugin, behind the generic page bridge."""

from __future__ import annotations

import asyncio
import base64
from collections import Counter
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from nahida_bot.plugins.meme_library.library import MemeLibrary, MemeMetadata


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _List(_Payload):
    query: str = Field(default="", max_length=200)
    category: str = Field(default="", max_length=120)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=12, ge=1, le=40)


class _Asset(_Payload):
    meme_id: str = Field(pattern=r"^[0-9a-f]{20}$")


class _Update(_Asset):
    title: str = Field(max_length=120)
    tags: list[str] = Field(default_factory=list, max_length=32)
    description: str = Field(default="", max_length=500)


class MemeLibraryAdmin:
    """The host owns authentication; the plugin owns validation and persistence."""

    def __init__(
        self, library: MemeLibrary, lock: asyncio.Lock, *, collect_enabled: bool = False
    ) -> None:
        self._library, self._lock = library, lock
        self._collect_enabled = collect_enabled

    async def handle(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Dispatch only the management actions explicitly implemented here."""
        handlers = {
            "list": self._list,
            "preview": self._preview,
            "update": self._update,
            "delete": self._delete,
        }
        handler = handlers.get(action)
        if handler is None:
            raise ValueError("Unknown meme library action")
        async with self._lock:
            # Preview requests verify one asset rather than rescanning every file.
            if action == "list":
                await asyncio.to_thread(self._library.refresh)
            return await handler(payload)

    async def _list(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = _List.model_validate(payload)
        matches = self._library.search(
            request.query, request.category, self._library.max_items
        )
        counts = Counter(meme.category for meme in self._library.items.values())
        return {
            "total": len(self._library.items),
            "matched": len(matches),
            "categories": dict(sorted(counts.items())),
            "memes": [
                meme.admin_public()
                for meme in matches[request.offset : request.offset + request.limit]
            ],
            "skipped": self._library.skipped,
            "truncated": self._library.truncated,
            "collect_enabled": self._collect_enabled,
        }

    async def _preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = _Asset.model_validate(payload)
        meme, data = await asyncio.to_thread(self._library.read_asset, request.meme_id)
        return {
            "data_url": f"data:{meme.mime_type};base64,{base64.b64encode(data).decode('ascii')}"
        }

    async def _update(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = _Update.model_validate(payload)
        metadata = MemeMetadata(
            title=request.title, tags=request.tags, description=request.description
        )
        meme = await asyncio.to_thread(
            self._library.update_metadata,
            request.meme_id,
            title=metadata.title,
            tags=metadata.tags,
            description=metadata.description,
        )
        return {"meme": meme.admin_public()}

    async def _delete(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = _Asset.model_validate(payload)
        removed = await asyncio.to_thread(self._library.delete, request.meme_id)
        return {"deleted": True, "removed_files": removed}
