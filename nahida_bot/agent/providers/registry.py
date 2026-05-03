"""Provider registration and factory utilities."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from nahida_bot.agent.providers.base import ChatProvider

TProvider = TypeVar("TProvider", bound="type[ChatProvider]")
ProviderFactory = Callable[[dict[str, Any]], "ChatProvider"]


@dataclass(slots=True, frozen=True)
class ProviderDescriptor:
    """Metadata for a registered provider class."""

    provider_type: str
    description: str
    cls: type[ChatProvider] | None = None
    factory: ProviderFactory | None = None
    config_schema: dict[str, Any] = field(default_factory=dict)
    owner_plugin_id: str = ""


_REGISTRY: dict[str, ProviderDescriptor] = {}
_RUNTIME_REGISTRY: dict[str, ProviderDescriptor] = {}

# Built-in providers live for the process lifetime. Runtime providers are
# plugin-owned and must be removable for disable/reload and test isolation.


def register_provider(provider_type: str, description: str = ""):  # noqa: ANN201 — returns decorator
    """Decorator: register a ``ChatProvider`` subclass by type name.

    Args:
        provider_type: Unique identifier for the provider (e.g. ``"deepseek"``).
        description: Human-readable description shown in listings.

    Raises:
        ValueError: If *provider_type* is already registered.
    """

    def decorator(cls: TProvider) -> TProvider:
        if provider_type in _REGISTRY:
            raise ValueError(f"Provider type '{provider_type}' already registered")
        if provider_type in _RUNTIME_REGISTRY:
            raise ValueError(
                f"Provider type '{provider_type}' already registered at runtime"
            )
        _REGISTRY[provider_type] = ProviderDescriptor(
            provider_type=provider_type,
            description=description,
            cls=cls,
        )
        return cls

    return decorator


def get_provider_class(provider_type: str) -> type[ChatProvider] | None:
    """Look up a registered provider class by its type name."""
    descriptor = _REGISTRY.get(provider_type)
    return descriptor.cls if descriptor else None


def register_runtime_provider(
    provider_type: str,
    factory: ProviderFactory,
    *,
    description: str = "",
    config_schema: dict[str, Any] | None = None,
    owner_plugin_id: str = "",
) -> None:
    """Register a provider factory at runtime, usually from a plugin."""
    if provider_type in _REGISTRY or provider_type in _RUNTIME_REGISTRY:
        raise ValueError(f"Provider type '{provider_type}' already registered")
    _RUNTIME_REGISTRY[provider_type] = ProviderDescriptor(
        provider_type=provider_type,
        description=description,
        factory=factory,
        config_schema=config_schema or {},
        owner_plugin_id=owner_plugin_id,
    )


def unregister_runtime_provider(
    provider_type: str, *, owner_plugin_id: str = ""
) -> bool:
    """Unregister a runtime provider factory.

    If ``owner_plugin_id`` is provided, only the owning plugin may unregister it.
    Returns True when a runtime provider was removed.
    """
    descriptor = _RUNTIME_REGISTRY.get(provider_type)
    if descriptor is None:
        return False
    if owner_plugin_id and descriptor.owner_plugin_id != owner_plugin_id:
        return False
    del _RUNTIME_REGISTRY[provider_type]
    return True


def clear_runtime_providers(*, owner_plugin_id: str = "") -> int:
    """Clear runtime provider registrations.

    If ``owner_plugin_id`` is provided, only providers owned by that plugin are
    removed. Returns the number of removed provider types.
    """
    if owner_plugin_id:
        provider_types = [
            provider_type
            for provider_type, descriptor in _RUNTIME_REGISTRY.items()
            if descriptor.owner_plugin_id == owner_plugin_id
        ]
    else:
        provider_types = list(_RUNTIME_REGISTRY)

    for provider_type in provider_types:
        del _RUNTIME_REGISTRY[provider_type]
    return len(provider_types)


def create_provider(provider_type: str, **kwargs: object) -> ChatProvider:
    """Factory: create a provider instance by type name.

    Raises:
        ValueError: If *provider_type* is not registered.
    """
    descriptor = _REGISTRY.get(provider_type) or _RUNTIME_REGISTRY.get(provider_type)
    if descriptor is None:
        raise ValueError(f"Unknown provider type: {provider_type}")
    if descriptor.cls is not None:
        return descriptor.cls(**kwargs)  # type: ignore[call-arg]
    if descriptor.factory is not None:
        return descriptor.factory(dict(kwargs))
    raise ValueError(f"Provider type '{provider_type}' has no factory")


def list_providers() -> list[ProviderDescriptor]:
    """Return all registered provider descriptors."""
    return list(_REGISTRY.values()) + list(_RUNTIME_REGISTRY.values())
