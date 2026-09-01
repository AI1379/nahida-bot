"""UID bindings backed by nahida-bot's plugin-scoped data store."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping
from typing import Any, Protocol

from .domain import Game

_KEY_PREFIX = "binding:"


class PluginDataPort(Protocol):
    async def plugin_data_get(self, key: str) -> Any | None: ...

    async def plugin_data_set(self, key: str, value: Any) -> None: ...

    async def plugin_data_delete(self, key: str) -> bool: ...


class BindingStore:
    """Persist non-secret game UIDs for one platform user."""

    def __init__(self, data: PluginDataPort) -> None:
        self._data = data
        self._write_lock = asyncio.Lock()

    async def get_all(self, actor_key: str) -> dict[Game, int]:
        raw = await self._data.plugin_data_get(self._key(actor_key))
        if not isinstance(raw, Mapping):
            return {}

        raw_bindings = raw.get("bindings")
        if not isinstance(raw_bindings, Mapping):
            return {}

        result: dict[Game, int] = {}
        for raw_game, raw_uid in raw_bindings.items():
            try:
                game = Game(str(raw_game))
                uid = int(raw_uid)
            except (TypeError, ValueError):
                continue
            if uid > 0:
                result[game] = uid
        return result

    async def bind(self, actor_key: str, game: Game, uid: int) -> None:
        async with self._write_lock:
            bindings = await self.get_all(actor_key)
            bindings[game] = uid
            await self._save(actor_key, bindings)

    async def replace(self, actor_key: str, bindings: Mapping[Game, int]) -> None:
        async with self._write_lock:
            if bindings:
                await self._save(actor_key, bindings)
            else:
                await self._data.plugin_data_delete(self._key(actor_key))

    async def unbind(self, actor_key: str, game: Game | None = None) -> bool:
        async with self._write_lock:
            bindings = await self.get_all(actor_key)
            if game is None:
                if not bindings:
                    return False
                return await self._data.plugin_data_delete(self._key(actor_key))

            if game not in bindings:
                return False
            bindings.pop(game)
            if not bindings:
                return await self._data.plugin_data_delete(self._key(actor_key))
            await self._save(actor_key, bindings)
            return True

    async def _save(self, actor_key: str, bindings: Mapping[Game, int]) -> None:
        await self._data.plugin_data_set(
            self._key(actor_key),
            {"bindings": {game.value: uid for game, uid in bindings.items()}},
        )

    @staticmethod
    def _key(actor_key: str) -> str:
        digest = hashlib.sha256(actor_key.encode("utf-8")).hexdigest()
        return f"{_KEY_PREFIX}{digest}"
