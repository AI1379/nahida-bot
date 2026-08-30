"""Gateway facet for the Pomodoro plugin."""

from __future__ import annotations

from nahida_bot.plugins.base import BotAPI, Plugin, PluginManifest
from nahida_bot.plugins.builtin.tools.desktop import (
    DESKTOP_POMODORO_TOOL,
    DesktopTools,
)
from nahida_bot.plugins.tooling import register_tool_definitions


class PomodoroPlugin(Plugin):
    """Expose agent control for the actor-bound Desktop timer."""

    def __init__(self, api: BotAPI, manifest: PluginManifest) -> None:
        super().__init__(api, manifest)
        self._desktop_tools = DesktopTools(api)

    async def on_load(self) -> None:
        definitions = [
            definition
            for definition in self._desktop_tools.definitions()
            if definition.name == DESKTOP_POMODORO_TOOL
        ]
        if len(definitions) != 1:
            raise RuntimeError("Pomodoro Desktop tool definition is unavailable")
        register_tool_definitions(self.api, definitions)
