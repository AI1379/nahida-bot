"""Tests for the Pomodoro plugin's Gateway facet."""

from __future__ import annotations

from typing import Any

from nahida_bot.plugins.manifest import PluginManifest
from nahida_bot.plugins.pomodoro.plugin import PomodoroPlugin


class _FakeAPI:
    def __init__(self) -> None:
        self.tools: dict[str, dict[str, Any]] = {}

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Any,
        *,
        requires_admin: bool = False,
        scope: str = "",
    ) -> None:
        self.tools[name] = {
            "description": description,
            "parameters": parameters,
            "handler": handler,
            "requires_admin": requires_admin,
            "scope": scope,
        }


async def test_gateway_facet_owns_only_the_pomodoro_tool() -> None:
    api = _FakeAPI()
    manifest = PluginManifest(
        id="nahida.pomodoro",
        name="Pomodoro",
        version="0.1.0",
        entrypoint="nahida_bot.plugins.pomodoro.plugin:PomodoroPlugin",
    )
    plugin = PomodoroPlugin(api, manifest)  # type: ignore[arg-type]

    await plugin.on_load()

    assert set(api.tools) == {"desktop_pomodoro"}
    assert api.tools["desktop_pomodoro"]["parameters"]["required"] == ["action"]
