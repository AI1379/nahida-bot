"""CalDAV provider services shared by the TODO and Calendar plugins."""

from typing import Any

from nahida_bot_sdk.plugin import Plugin
from nahida_bot_sdk.services import PluginServiceContext

from .config import CalDAVConfig
from .events import EventProvider
from .runtime import CalDAVRuntime
from .tasks import TaskProvider


class CalDAVPlugin(Plugin):
    async def on_load(self) -> None:
        self.runtime = CalDAVRuntime(
            self.api, CalDAVConfig.model_validate(self.manifest.config)
        )
        self.tasks = TaskProvider(self.runtime)
        self.events = EventProvider(self.runtime)
        self.api.register_plugin_service("tasks.v1", self.task_call)
        self.api.register_plugin_service("calendar.v1", self.calendar_call)

    async def task_call(
        self, operation: str, arguments: dict[str, Any], context: PluginServiceContext
    ) -> dict[str, Any]:
        return await self.tasks.handle(operation, arguments, context.actor_account_key)

    async def calendar_call(
        self, operation: str, arguments: dict[str, Any], context: PluginServiceContext
    ) -> dict[str, Any]:
        return await self.events.handle(operation, arguments, context.actor_account_key)
