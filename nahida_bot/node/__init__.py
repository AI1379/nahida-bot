"""Python Node Client SDK for the Gateway-Node protocol.

See ``docs/architecture/gateway-node-protocol.md`` for the protocol. This
package lets Python processes act as nodes (workers, tool-hosts, test
fixtures) without reimplementing the wire protocol.
"""

from nahida_bot.node.capabilities import (
    CapabilityHandler,
    CapabilityRegistration,
    CapabilityRegistry,
)
from nahida_bot.node.client import (
    NodeClient,
    RECONNECT_INITIAL_DELAY,
    RECONNECT_MAX_DELAY,
)

__all__ = [
    "CapabilityHandler",
    "CapabilityRegistration",
    "CapabilityRegistry",
    "NodeClient",
    "RECONNECT_INITIAL_DELAY",
    "RECONNECT_MAX_DELAY",
]
