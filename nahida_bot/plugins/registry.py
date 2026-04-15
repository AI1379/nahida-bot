"""Central registries for tools and event handlers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(slots=True, frozen=True)
class ToolEntry:
    """A registered tool with its metadata and handler."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    handler: Callable[..., Awaitable[str]]
    plugin_id: str


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
