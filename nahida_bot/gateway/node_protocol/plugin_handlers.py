"""Authenticated plugin calls and bounded state updates over the node socket."""

import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from nahida_bot.gateway.node_protocol.dispatcher import InboundHandlerResult
from nahida_bot.gateway.node_protocol.schemas import NodeErrorObject
from nahida_bot.gateway.services.desktop_delivery import DesktopContextUpdate
from nahida_bot_sdk.services import PluginServiceContext


class PluginCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str = Field(min_length=1, max_length=128)
    operation: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)


def register_plugin_handlers(dispatcher: Any, services: Any) -> None:
    async def update_context(session: Any, envelope: Any) -> InboundHandlerResult:
        update = DesktopContextUpdate.model_validate(envelope.payload)
        if session.node_type != "desktop":
            raise PermissionError("Only Desktop nodes may report Desktop context")
        previous = session.context_state.get("sequence", -1)
        if update.sequence > previous:
            session.context_state = update.model_dump()
            session.context_received_at = time.monotonic()
        return InboundHandlerResult(payload={"accepted": update.sequence > previous})

    async def call_service(session: Any, envelope: Any) -> InboundHandlerResult:
        try:
            request = PluginCall.model_validate(envelope.payload)
            if session.node_type != "desktop" or services is None:
                raise PermissionError("Desktop plugin services are unavailable")
            result = await services.call(
                request.service,
                request.operation,
                request.arguments,
                PluginServiceContext(
                    session.actor_account_key, node_id=session.node_id
                ),
                desktop=True,
            )
            return InboundHandlerResult(payload=result)
        except (ValueError, PermissionError) as exc:
            return InboundHandlerResult(
                ok=False,
                error=NodeErrorObject(code="plugin_request_rejected", message=str(exc)),
            )

    dispatcher.register_inbound_handler("node.context.update", update_context)
    dispatcher.register_inbound_handler("plugin.service.call", call_service)
