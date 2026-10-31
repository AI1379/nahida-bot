"""Versioned, in-process plugin service contracts shared with Desktop clients."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PluginServiceContext:
    """Host-authenticated actor; never reconstructed from request arguments."""

    actor_account_key: str
    caller_plugin_id: str = ""
    node_id: str = ""


PluginServiceHandler = Callable[
    [str, dict[str, Any], PluginServiceContext], Awaitable[dict[str, Any]]
]
