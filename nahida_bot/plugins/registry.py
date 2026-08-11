"""Central registries for tools, event handlers, and prompt supplements."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from nahida_bot_sdk.messaging import MessageContext


@dataclass(slots=True, frozen=True)
class ToolEntry:
    """A registered tool with its metadata and handler."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    handler: Callable[..., Awaitable[str]]
    plugin_id: str
    requires_admin: bool = False


@dataclass(slots=True, frozen=True)
class HandlerEntry:
    """A registered event handler with ownership tracking."""

    event_type: type
    handler: Callable[..., Awaitable[None]]
    plugin_id: str


class ToolRegistry:
    """Registry mapping tool names to their definitions and handlers."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolEntry] = {}

    def register(self, entry: ToolEntry) -> None:
        """Register a tool. Raises KeyError if the name is already taken."""
        if entry.name in self._tools:
            existing = self._tools[entry.name]
            raise KeyError(
                f"Tool '{entry.name}' is already registered by plugin "
                f"'{existing.plugin_id}'"
            )
        self._tools[entry.name] = entry

    def unregister(self, name: str) -> None:
        """Remove a tool by name."""
        self._tools.pop(name, None)

    def get(self, name: str) -> ToolEntry | None:
        """Look up a tool by name."""
        return self._tools.get(name)

    def all(self) -> list[ToolEntry]:
        """Return all registered tools."""
        return list(self._tools.values())

    def unregister_by_plugin(self, plugin_id: str) -> int:
        """Remove all tools owned by a plugin. Returns count removed."""
        to_remove = [
            name for name, entry in self._tools.items() if entry.plugin_id == plugin_id
        ]
        for name in to_remove:
            self._tools.pop(name, None)
        return len(to_remove)


class HandlerRegistry:
    """Registry tracking which plugin registered which event handlers."""

    def __init__(self) -> None:
        self._handlers: list[HandlerEntry] = []

    def register(self, entry: HandlerEntry) -> None:
        """Record an event handler registration."""
        self._handlers.append(entry)

    def unregister(
        self,
        handler: Callable[..., Awaitable[None]],
        plugin_id: str,
    ) -> bool:
        """Remove one handler owned by a plugin. Returns whether it was found."""
        for index, entry in enumerate(self._handlers):
            if entry.plugin_id == plugin_id and entry.handler is handler:
                self._handlers.pop(index)
                return True
        return False

    def unregister_by_plugin(self, plugin_id: str) -> list[HandlerEntry]:
        """Remove all handlers owned by a plugin. Returns removed entries."""
        kept: list[HandlerEntry] = []
        removed: list[HandlerEntry] = []
        for entry in self._handlers:
            if entry.plugin_id == plugin_id:
                removed.append(entry)
            else:
                kept.append(entry)
        self._handlers = kept
        return removed

    def handlers_for_plugin(self, plugin_id: str) -> list[HandlerEntry]:
        """Return all handlers owned by a plugin."""
        return [e for e in self._handlers if e.plugin_id == plugin_id]


@dataclass(slots=True, frozen=True)
class PromptSupplementEntry:
    """A registered prompt supplement with conditional injection logic."""

    key: str
    instruction: str
    plugin_id: str
    channel: str | None = None
    filter: Callable[["MessageContext"], bool] | None = field(default=None, repr=False)


class PromptSupplementRegistry:
    """Registry mapping keys to prompt supplement entries with match logic."""

    def __init__(self) -> None:
        self._entries: dict[str, PromptSupplementEntry] = {}

    def register(self, entry: PromptSupplementEntry) -> None:
        """Register a prompt supplement. Raises KeyError if the key is already taken."""
        if entry.key in self._entries:
            existing = self._entries[entry.key]
            raise KeyError(
                f"Prompt supplement '{entry.key}' is already registered by plugin "
                f"'{existing.plugin_id}'"
            )
        self._entries[entry.key] = entry

    def unregister(self, key: str) -> None:
        """Remove a prompt supplement by key."""
        self._entries.pop(key, None)

    def get(self, key: str) -> PromptSupplementEntry | None:
        """Look up a prompt supplement by key."""
        return self._entries.get(key)

    def all(self) -> list[PromptSupplementEntry]:
        """Return all registered prompt supplements."""
        return list(self._entries.values())

    def unregister_by_plugin(self, plugin_id: str) -> int:
        """Remove all prompt supplements owned by a plugin. Returns count removed."""
        to_remove = [k for k, e in self._entries.items() if e.plugin_id == plugin_id]
        for k in to_remove:
            self._entries.pop(k, None)
        return len(to_remove)

    def get_matching(self, context: "MessageContext") -> list[str]:
        """Return instruction strings for all entries whose conditions match."""
        results: list[str] = []
        for entry in self._entries.values():
            if entry.channel is not None and entry.channel != context.channel:
                continue
            if entry.filter is not None and not entry.filter(context):
                continue
            results.append(entry.instruction)
        return results


@dataclass(slots=True, frozen=True)
class StatusProviderEntry:
    """A registered status provider that contributes text to /status output."""

    key: str
    label: str
    handler: Callable[..., Awaitable[str | None]]
    plugin_id: str


class StatusProviderRegistry:
    """Registry mapping keys to status provider entries."""

    def __init__(self) -> None:
        self._entries: dict[str, StatusProviderEntry] = {}

    def register(self, entry: StatusProviderEntry) -> None:
        """Register a status provider. Raises KeyError if key is taken."""
        if entry.key in self._entries:
            existing = self._entries[entry.key]
            raise KeyError(
                f"Status provider '{entry.key}' is already registered by plugin "
                f"'{existing.plugin_id}'"
            )
        self._entries[entry.key] = entry

    def unregister(self, key: str) -> None:
        """Remove a status provider by key."""
        self._entries.pop(key, None)

    def all(self) -> list[StatusProviderEntry]:
        """Return all registered status providers."""
        return list(self._entries.values())

    def unregister_by_plugin(self, plugin_id: str) -> int:
        """Remove all status providers owned by a plugin. Returns count removed."""
        to_remove = [k for k, e in self._entries.items() if e.plugin_id == plugin_id]
        for k in to_remove:
            self._entries.pop(k, None)
        return len(to_remove)
