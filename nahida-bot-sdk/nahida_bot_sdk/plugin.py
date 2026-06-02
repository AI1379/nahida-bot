"""Plugin base class, SessionInfo, and MemoryRef."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from nahida_bot_sdk.api import BotAPI
from nahida_bot_sdk.manifest import PluginManifest


@dataclass(slots=True, frozen=True)
class SessionInfo:
    """Snapshot of an active session."""

    session_id: str
    channel: str
    chat_id: str
    user_id: str
    workspace_id: str = ""


@dataclass(slots=True, frozen=True)
class MemoryRef:
    """A retrieved memory record."""

    key: str
    content: str
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class Plugin(ABC):
    """Base class for all nahida-bot plugins.

    Subclass and implement ``on_load`` to register event handlers and tools.
    Optionally override ``on_unload``, ``on_enable``, and ``on_disable``
    for lifecycle management.
    """

    def __init__(self, api: BotAPI, manifest: PluginManifest) -> None:
        self._api = api
        self._manifest = manifest

    @property
    def api(self) -> BotAPI:
        """Bot capabilities available to this plugin."""
        return self._api

    @property
    def manifest(self) -> PluginManifest:
        """This plugin's manifest metadata."""
        return self._manifest

    @abstractmethod
    async def on_load(self) -> None:
        """Called when the plugin is loaded. Register handlers/tools here."""
        ...

    async def on_unload(self) -> None:
        """Called when the plugin is being unloaded. Clean up resources."""
        pass

    async def on_enable(self) -> None:
        """Called when the plugin is enabled after loading."""
        pass

    async def on_disable(self) -> None:
        """Called when the plugin is being disabled."""
        pass
