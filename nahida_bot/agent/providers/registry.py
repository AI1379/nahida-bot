"""Provider registration and factory utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from nahida_bot.agent.providers.base import ChatProvider

TProvider = TypeVar("TProvider", bound="type[ChatProvider]")


@dataclass(slots=True, frozen=True)
class ProviderDescriptor:
    """Metadata for a registered provider class."""

    provider_type: str
    description: str
    cls: type[ChatProvider]


_REGISTRY: dict[str, ProviderDescriptor] = {}

# TODO: _REGISTRY is a module-level mutable global — tests cannot isolate or
# reset state between runs. Add a ``reset_registry()`` helper or switch to a
# class-based registry that can be instantiated per-test.


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


def create_provider(provider_type: str, **kwargs: object) -> ChatProvider:
    """Factory: create a provider instance by type name.

    Raises:
        ValueError: If *provider_type* is not registered.
    """
    cls = get_provider_class(provider_type)
    if cls is None:
        raise ValueError(f"Unknown provider type: {provider_type}")
    return cls(**kwargs)  # type: ignore[call-arg]


def list_providers() -> list[ProviderDescriptor]:
    """Return all registered provider descriptors."""
    return list(_REGISTRY.values())
