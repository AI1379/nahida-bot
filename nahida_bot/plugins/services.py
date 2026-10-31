"""Small lifecycle-owned registry for versioned plugin services."""

import asyncio
from dataclasses import dataclass
from typing import Any

from nahida_bot_sdk.services import PluginServiceContext, PluginServiceHandler


@dataclass(frozen=True, slots=True)
class ServiceEntry:
    owner: str
    name: str
    handler: PluginServiceHandler
    desktop: bool = False


class PluginServiceRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, ServiceEntry] = {}

    def register(self, entry: ServiceEntry) -> None:
        key = f"{entry.owner}:{entry.name}"
        if key in self._entries:
            raise ValueError(f"Plugin service already registered: {key}")
        self._entries[key] = entry

    def unregister_owner(self, owner: str) -> None:
        self._entries = {
            key: entry for key, entry in self._entries.items() if entry.owner != owner
        }

    async def call(
        self,
        service: str,
        operation: str,
        arguments: dict[str, Any],
        context: PluginServiceContext,
        *,
        desktop: bool = False,
    ) -> dict[str, Any]:
        entry = self._entries.get(service)
        if entry is None:
            raise ValueError(f"Plugin service unavailable: {service}")
        if desktop and not entry.desktop:
            raise PermissionError("This service is not exposed to Desktop")
        if not context.actor_account_key:
            raise PermissionError("An authenticated account is required")
        async with asyncio.timeout(15):
            return await entry.handler(operation, arguments, context)
