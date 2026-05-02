"""Multi-provider manager — resolves providers by id or model name."""

from __future__ import annotations

from dataclasses import dataclass, field

from nahida_bot.agent.context import ContextBuilder
from nahida_bot.agent.providers.base import ChatProvider


@dataclass(slots=True)
class ProviderSlot:
    """One instantiated provider with its config and available models."""

    id: str
    provider: ChatProvider
    context_builder: ContextBuilder
    default_model: str
    available_models: list[str] = field(default_factory=list)


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

    def resolve_model(self, model_name: str) -> ProviderSlot | None:
        """Find which provider serves a given model name.

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
                if not slot.available_models or bare_model in slot.available_models:
                    return slot

        for slot in self._slots.values():
            if not slot.available_models or model_name in slot.available_models:
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
