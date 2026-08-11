"""Tests for composable plugin tool definitions."""

from __future__ import annotations

from typing import Any

from nahida_bot.plugins.tooling import (
    PluginToolDefinition,
    register_tool_definitions,
)


class _RecordingRegistrar:
    def __init__(self) -> None:
        self.tools: list[tuple[str, str, dict[str, Any], Any, bool]] = []

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Any,
        *,
        requires_admin: bool = False,
    ) -> None:
        self.tools.append((name, description, parameters, handler, requires_admin))


async def _handler(value: str) -> str:
    return value


def test_register_tool_definitions_preserves_declaration_order() -> None:
    registrar = _RecordingRegistrar()
    empty_schema = {"type": "object", "properties": {}}
    definitions = (
        PluginToolDefinition("first", "First", empty_schema, _handler),
        PluginToolDefinition(
            "second",
            "Second",
            empty_schema,
            _handler,
            requires_admin=True,
        ),
    )

    register_tool_definitions(registrar, definitions)

    assert [tool[0] for tool in registrar.tools] == ["first", "second"]
    assert registrar.tools[0][1:] == ("First", empty_schema, _handler, False)
    assert registrar.tools[1][-1] is True
