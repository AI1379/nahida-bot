"""Small shared primitives for staged plugin runtime-service updates."""

from __future__ import annotations


class RuntimeServiceUnset:
    """Marker used when a service was omitted from an update call."""

    __slots__ = ()


RUNTIME_SERVICE_UNSET = RuntimeServiceUnset()
