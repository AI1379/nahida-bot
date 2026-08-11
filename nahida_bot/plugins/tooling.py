"""Composable definitions for plugin-provided agent tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Protocol


ToolHandler = Callable[..., Awaitable[str]]


class ToolRegistrar(Protocol):
    """Minimal registration surface required by plugin tool collections."""

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: ToolHandler,
        *,
        requires_admin: bool = False,
    ) -> None: ...


@dataclass(slots=True, frozen=True)
class PluginToolDefinition:
    """One plugin-owned tool before runtime ownership is attached."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    requires_admin: bool = False

    def register(self, registrar: ToolRegistrar) -> None:
        """Register this definition through the stable plugin API."""
        registrar.register_tool(
            self.name,
            self.description,
            self.parameters,
            self.handler,
            requires_admin=self.requires_admin,
        )


def register_tool_definitions(
    registrar: ToolRegistrar,
    definitions: Iterable[PluginToolDefinition],
) -> None:
    """Register a cohesive set of plugin tools in declaration order."""
    for definition in definitions:
        definition.register(registrar)
