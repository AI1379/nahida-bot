"""Multi-provider manager — resolves providers by id or model name."""

from __future__ import annotations

from dataclasses import dataclass, field

from nahida_bot.agent.context import ContextBuilder
from nahida_bot.agent.providers.base import ChatProvider, ModelCapabilities


@dataclass(slots=True)
class ProviderSlot:
    """One instantiated provider with its config and available models."""

    id: str
    provider: ChatProvider
    context_builder: ContextBuilder
    default_model: str
    available_models: list[str] = field(default_factory=list)
    capabilities_by_model: dict[str, ModelCapabilities] = field(default_factory=dict)

    def supports_model(self, model: str) -> bool:
        """Return whether this provider slot can serve ``model``."""
        return not self.available_models or model in self.available_models

    def resolve_capabilities(self, model: str | None = None) -> ModelCapabilities:
        """Return capabilities for a specific model, falling back to slot default."""
        resolved_model = model or self.default_model
        if resolved_model in self.capabilities_by_model:
            return self.capabilities_by_model[resolved_model]
        if self.default_model in self.capabilities_by_model:
            return self.capabilities_by_model[self.default_model]
        return ModelCapabilities()


class ProviderManager:
    """Manages multiple LLM providers and resolves per-request."""

    def __init__(self, slots: list[ProviderSlot], default_id: str = "") -> None:
        self._slots: dict[str, ProviderSlot] = {s.id: s for s in slots}
        if default_id:
            self._default_id = default_id
        elif slots:
            self._default_id = slots[0].id
        else:
            self._default_id = ""

    @property
    def default(self) -> ProviderSlot | None:
        """Return the default provider slot."""
        return self._slots.get(self._default_id)

    def get(self, provider_id: str) -> ProviderSlot | None:
        """Look up a provider slot by id."""
        return self._slots.get(provider_id)

    def resolve_model_selection(
        self, model_name: str
    ) -> tuple[ProviderSlot, str] | None:
        """Find the provider and provider-local model name for ``model_name``.

        Accepts both bare model names (``"MiniMax-M2.5"``) and compound
        ``provider_id/model_name`` format (``"minimax/MiniMax-M2.5"``).
        When a compound name is given, the prefix is matched against slot
        ids first; if it matches, the suffix is validated against that
        slot's ``available_models``.  If the prefix does not match any
        slot, the full string is treated as a bare model name (covers
        model names that happen to contain ``/``).

        If a slot's ``available_models`` is empty, it matches any model
        (the provider may accept dynamic model names).
        """
        if "/" in model_name:
            provider_id, _, bare_model = model_name.partition("/")
            slot = self._slots.get(provider_id)
            if slot is not None:
                if slot.supports_model(bare_model):
                    return slot, bare_model
                return None

        for slot in self._slots.values():
            if slot.supports_model(model_name):
                return slot, model_name
        return None

    def resolve_model(self, model_name: str) -> ProviderSlot | None:
        """Find which provider serves a given model name."""
        resolved = self.resolve_model_selection(model_name)
        if resolved is None:
            return None
        slot, _ = resolved
        return slot
        return None

    def list_available(self) -> list[dict[str, str]]:
        """Return all available provider+model combinations."""
        results: list[dict[str, str]] = []
        for slot in self._slots.values():
            models = slot.available_models or [slot.default_model]
            for model in models:
                results.append({"provider_id": slot.id, "model": model})
        return results

    @property
    def slot_ids(self) -> list[str]:
        """Return all registered provider slot ids."""
        return list(self._slots.keys())
