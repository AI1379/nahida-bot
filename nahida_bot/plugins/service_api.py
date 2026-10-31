"""Service registration and atomic plugin storage additions to the API bridge."""

from typing import Any

from nahida_bot.core.context import current_session
from nahida_bot.plugins.services import PluginServiceRegistry, ServiceEntry
from nahida_bot_sdk.services import PluginServiceContext, PluginServiceHandler


class PluginServiceAPI:
    _plugin_id: str
    _registrations_active: bool
    _permissions: Any
    _plugin_data_repo: Any
    _service_registry: PluginServiceRegistry | None
    _registered_services: dict[str, ServiceEntry]

    @property
    def actor_account_key(self) -> str:
        context = current_session.get()
        return context.actor_account_key if context else ""

    def register_plugin_service(
        self,
        name: str,
        handler: PluginServiceHandler,
        *,
        desktop: bool = False,
    ) -> None:
        if not name or ":" in name or len(name) > 64:
            raise ValueError("Service name must be a short local versioned name")
        if name in self._registered_services:
            raise ValueError(f"Duplicate service: {name}")
        entry = ServiceEntry(self._plugin_id, name, handler, desktop)
        self._registered_services[name] = entry
        if self._registrations_active and self._service_registry is not None:
            self._service_registry.register(entry)

    async def call_plugin_service(
        self,
        service: str,
        operation: str,
        arguments: dict[str, Any],
        *,
        actor_account_key: str = "",
    ) -> dict[str, Any]:
        if not self._registrations_active or self._service_registry is None:
            raise RuntimeError("Plugin services are not active")
        actor = actor_account_key or self.actor_account_key
        context = PluginServiceContext(actor, caller_plugin_id=self._plugin_id)
        return await self._service_registry.call(service, operation, arguments, context)

    async def plugin_data_compare_and_set(
        self,
        key: str,
        expected: Any,
        value: Any,
    ) -> bool:
        self._permissions.check_plugin_data_read()
        self._permissions.check_plugin_data_write()
        if self._plugin_data_repo is None:
            raise RuntimeError("Persistent plugin storage is unavailable")
        return await self._plugin_data_repo.compare_and_set(
            self._plugin_id,
            key,
            expected,
            value,
        )
