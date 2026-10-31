"""Actor-bound Desktop transport; routing policy belongs to plugins."""

import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from nahida_bot_sdk.services import PluginServiceContext


class DesktopContextUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1] = 1
    sequence: int = Field(ge=0)
    locked: bool | None = None
    activity: Literal["active", "idle", "unknown"] = "unknown"
    do_not_disturb: bool = False
    ui_visible: bool = False


class DesktopDeliveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: str = Field(min_length=1, max_length=128)
    notification_id: str = Field(min_length=1, max_length=160)
    title: str = Field(max_length=80)
    body: str = Field(min_length=1, max_length=12000)


class DesktopDeliveryService:
    """Expose only this actor's endpoints and text-only delivery capability."""

    def __init__(self, registry: Any, invoker: Any) -> None:
        self.registry = registry
        self.invoker = invoker

    async def handle(
        self,
        operation: str,
        arguments: dict[str, Any],
        context: PluginServiceContext,
    ) -> dict[str, Any]:
        if operation == "endpoints":
            endpoints = []
            for summary in self.registry.list_online_nodes():
                session = self.registry.get_online_session(summary["node_id"])
                if not self._eligible(session, context.actor_account_key):
                    continue
                state = session.context_state
                fresh = time.monotonic() - session.context_received_at <= 90
                endpoints.append(
                    {
                        "node_id": session.node_id,
                        "state": dict(state) if fresh else {},
                        "fresh": bool(fresh and state),
                    }
                )
            return {"endpoints": endpoints}
        if operation != "deliver":
            raise ValueError("Unknown Desktop transport operation")
        request = DesktopDeliveryRequest.model_validate(arguments)
        session = self.registry.get_online_session(request.node_id)
        if not self._eligible(session, context.actor_account_key):
            return {"ok": False, "error": "desktop_unavailable", "definite": True}
        result = await self.invoker.invoke(
            capability="desktop.notification.deliver",
            arguments=request.model_dump(exclude={"node_id"}),
            node_id=request.node_id,
            caller=f"plugin:{context.caller_plugin_id}",
        )
        return {
            "ok": result.ok,
            "receipt": result.payload if result.ok else None,
            "error": result.error.code if result.error else "",
            # A timeout may occur after the client accepted the notification.
            "definite": bool(result.ok),
        }

    @staticmethod
    def _eligible(session: Any, actor: str) -> bool:
        return bool(
            session
            and actor
            and session.actor_account_key == actor
            and session.node_type == "desktop"
            and session.get_capability("desktop.notification.deliver")
        )


def register_desktop_transport(manager: Any, registry: Any, invoker: Any) -> None:
    """Install the transport only where both the plugin host and nodes exist."""
    if manager is None or registry is None or invoker is None:
        return
    from nahida_bot.plugins.services import ServiceEntry

    transport = DesktopDeliveryService(registry, invoker)
    manager.service_registry.unregister_owner("host.desktop")
    manager.service_registry.register(
        ServiceEntry("host.desktop", "v1", transport.handle)
    )
