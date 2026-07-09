"""Capability registration helpers for Python node clients.

A node declares capabilities at registration time and provides handlers for
incoming ``capability.invoke`` requests. This module gives node authors a small,
typed surface so they don't deal with raw envelopes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from nahida_bot.gateway.node_protocol.schemas import NodeCapability

#: A capability handler receives the invoke arguments and returns either a
#: dict payload (success) or raises an exception (mapped to an error response).
CapabilityHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]]


@dataclass
class CapabilityRegistration:
    """A capability plus its handler and metadata."""

    spec: NodeCapability
    handler: CapabilityHandler
    timeout: float = 30.0

    async def invoke(self, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self.handler(arguments)
        return result or {}


class CapabilityRegistry:
    """Per-node registry of capabilities and their handlers."""

    def __init__(self) -> None:
        self._caps: dict[str, CapabilityRegistration] = {}

    def register(
        self,
        name: str,
        handler: CapabilityHandler,
        *,
        version: str = "1.0",
        direction: str = "gateway_to_node",
        risk: str = "low",
        description: str = "",
        timeout: float = 30.0,
        requires_user_approval: bool = False,
    ) -> NodeCapability:
        """Register a capability and return the spec for registration."""
        spec = NodeCapability(
            name=name,
            version=version,
            direction=direction,  # type: ignore[arg-type]
            risk=risk,  # type: ignore[arg-type]
            description=description,
            requires_user_approval=requires_user_approval,
        )
        self._caps[name] = CapabilityRegistration(
            spec=spec, handler=handler, timeout=timeout
        )
        return spec

    def unregister(self, name: str) -> bool:
        return self._caps.pop(name, None) is not None

    def get(self, name: str) -> CapabilityRegistration | None:
        return self._caps.get(name)

    def specs(self) -> list[NodeCapability]:
        return [reg.spec for reg in self._caps.values()]

    async def invoke(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        reg = self._caps.get(name)
        if reg is None:
            raise KeyError(name)
        try:
            return await asyncio.wait_for(reg.invoke(arguments), timeout=reg.timeout)
        except TimeoutError as exc:
            raise TimeoutError(f"capability {name} timed out") from exc


__all__ = ["CapabilityHandler", "CapabilityRegistration", "CapabilityRegistry"]
