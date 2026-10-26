"""Let the active agent select a received reaction image for its shared library."""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.context import current_session
from nahida_bot.plugins.base import BotAPI
from nahida_bot.plugins.meme_library.config import MemeLibraryConfig
from nahida_bot.plugins.meme_library.library import MemeLibrary, MemeMetadata


class MemeCollector:
    """Collect only actual image attachments of this turn, on an explicit tool call."""

    def __init__(
        self,
        api: BotAPI,
        config: MemeLibraryConfig,
        library: MemeLibrary,
        lock: asyncio.Lock,
        current_address: Callable[[], ChatAddress],
    ) -> None:
        self._api, self._config, self._library = api, config, library
        self._lock, self._current_address = lock, current_address
        self._last_collect: OrderedDict[str, float] = OrderedDict()

    def register(self) -> None:
        """Offer voluntary collection without saving every received image."""
        self._api.register_tool(
            "meme_collect",
            "Choose and save a reusable meme/reaction image received on THIS turn "
            "to the shared meme library. Use image_index (1-based among this turn's "
            "image attachments; default 1). You may choose this yourself when an "
            "image is useful for future expression; do not collect every image. "
            "Avoid personal photos, private information and ordinary screenshots. "
            "Describe its expression with accurate tags/title. No paths or URLs "
            "are accepted. No need to announce collection unless asked.",
            {
                "type": "object",
                "properties": {
                    "image_index": {"type": "integer", "minimum": 1, "default": 1},
                    "category": {"type": "string", "default": "default"},
                    "title": {"type": "string", "maxLength": 120},
                    "tags": {
                        "type": "array",
                        "maxItems": 32,
                        "items": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 80,
                        },
                    },
                    "description": {"type": "string", "maxLength": 500},
                },
                "additionalProperties": False,
            },
            self.collect,
        )

    async def collect(
        self,
        image_index: int = 1,
        category: str = "default",
        title: str = "",
        tags: list[str] | None = None,
        description: str = "",
    ) -> str:
        """Copy selected inbound bytes and record provenance supplied by the host."""
        try:
            if not self._config.collect_enabled:
                raise ValueError("Received-image collection is disabled")
            address = self._current_address()
            context = current_session.get()
            images = [
                (index, attachment)
                for index, attachment in enumerate(self._api.get_current_attachments())
                if attachment.kind == "image"
            ]
            if not 1 <= image_index <= len(images):
                raise ValueError(
                    f"Choose an image_index from 1 to {len(images)} on this turn"
                )
            attachment_index, attachment = images[image_index - 1]
            if attachment.file_size > self._config.max_image_bytes:
                raise ValueError("Image exceeds max_image_bytes")
            metadata = MemeMetadata(
                title=title or attachment.alt_text[:120] or "收藏的表情",
                tags=tags or [],
                description=description,
                source_chat=address.chat_key,
                source_user_id=context.user_id if context is not None else "",
                source_media_id=attachment.platform_id,
                collected_at=time.time(),
            )
            async with self._lock:
                self._reserve_cooldown(address.chat_key)
            media = await self._api.resolve_current_attachment(attachment_index)
            async with self._lock:
                await asyncio.to_thread(self._library.refresh)
                meme = await asyncio.to_thread(
                    self._library.import_file,
                    Path(media.path),
                    category=category,
                    metadata=metadata,
                )
            self._api.logger.info(
                "meme_library.collected", meme_id=meme.id, chat_address=address.chat_key
            )
            return json.dumps(
                {"saved": True, "meme": meme.public()}, ensure_ascii=False
            )
        except (OSError, ValueError) as exc:
            return f"Error: {exc}"
        except Exception:
            self._api.logger.exception("meme_library.collect_failed")
            return "Error: Could not collect the selected received image."

    def _reserve_cooldown(self, chat_key: str) -> None:
        now = time.monotonic()
        previous = self._last_collect.get(chat_key)
        if previous is not None:
            remaining = self._config.collect_cooldown_seconds - (now - previous)
            if remaining > 0:
                raise ValueError(
                    f"Collection cooldown; wait {math.ceil(remaining)} seconds"
                )
        self._last_collect[chat_key] = now
        self._last_collect.move_to_end(chat_key)
        while len(self._last_collect) > 4096:
            self._last_collect.popitem(last=False)
