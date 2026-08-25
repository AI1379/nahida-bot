"""Multi-provider manager — resolves providers by id or model name."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from time import monotonic
from typing import cast

from nahida_bot.agent.context import ContextBuilder
from nahida_bot.agent.providers.base import ChatProvider, ModelCapabilities
from nahida_bot.agent.providers.quota import (
    QuotaErrorKind,
    QuotaQueryError,
    QuotaReport,
)


@dataclass(slots=True)
class ProviderSlot:
    """One instantiated provider with its config and available models."""

    id: str
    provider: ChatProvider
    context_builder: ContextBuilder
    default_model: str
    available_models: list[str] = field(default_factory=list)
    capabilities_by_model: dict[str, ModelCapabilities] = field(default_factory=dict)
    tags_by_model: dict[str, list[str]] = field(default_factory=dict)

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
        self._quota_cache: dict[str, tuple[float, QuotaReport]] = {}
        self._quota_locks: dict[str, asyncio.Lock] = {}
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
        """Return the provider slot serving a model name."""
        resolved = self.resolve_model_selection(model_name)
        return resolved[0] if resolved is not None else None

    def list_available(self) -> list[dict[str, str]]:
        """Return all available provider+model combinations."""
        results: list[dict[str, str]] = []
        for slot in self._slots.values():
            models = slot.available_models or [slot.default_model]
            for model in models:
                results.append({"provider_id": slot.id, "model": model})
        return results

    @property
    def slots(self) -> list[ProviderSlot]:
        """Return all provider slots."""
        return list(self._slots.values())

    @property
    def slot_ids(self) -> list[str]:
        """Return all registered provider slot ids."""
        return list(self._slots.keys())

    async def query_quotas(
        self,
        provider_id: str = "",
        *,
        force_refresh: bool = False,
        cache_ttl_seconds: float = 60.0,
    ) -> list[QuotaReport]:
        """Query one or all providers, retaining a short-lived last result."""
        ids = [provider_id] if provider_id else self.slot_ids
        reports = list(
            await asyncio.gather(
                *(
                    self._query_one(
                        item,
                        force_refresh=force_refresh,
                        cache_ttl_seconds=cache_ttl_seconds,
                    )
                    for item in ids
                )
            )
        )
        if provider_id:
            return reports
        # /quota all should not list every ordinary Anthropic/OpenAI provider as
        # an error merely because it has no quota adapter configured.
        return [report for report in reports if report.error_kind != "unsupported"]

    async def _query_one(
        self,
        provider_id: str,
        *,
        force_refresh: bool,
        cache_ttl_seconds: float,
    ) -> QuotaReport:
        slot = self._slots.get(provider_id)
        if slot is None:
            return QuotaReport(
                provider_id=provider_id,
                error="Unknown provider",
                error_kind="request",
            )
        cached = self._quota_cache.get(provider_id)
        now = monotonic()
        if (
            not force_refresh
            and cached is not None
            and now - cached[0] < cache_ttl_seconds
        ):
            return QuotaReport(
                provider_id=provider_id,
                snapshot=cached[1].snapshot,
                error=cached[1].error,
                error_kind=cached[1].error_kind,
                cached=True,
            )

        lock = self._quota_locks.setdefault(provider_id, asyncio.Lock())
        async with lock:
            cached = self._quota_cache.get(provider_id)
            now = monotonic()
            if (
                not force_refresh
                and cached is not None
                and now - cached[0] < cache_ttl_seconds
            ):
                return QuotaReport(
                    provider_id=provider_id,
                    snapshot=cached[1].snapshot,
                    error=cached[1].error,
                    error_kind=cached[1].error_kind,
                    cached=True,
                )
            try:
                snapshot = await slot.provider.query_quota(provider_id=provider_id)
            except QuotaQueryError as exc:
                error_kind = cast(QuotaErrorKind, exc.kind)
                report = QuotaReport(
                    provider_id=provider_id,
                    error=str(exc),
                    error_kind=error_kind,
                )
                # Keep the last successful snapshot for transient outages.
                if exc.kind == "transient" and cached and cached[1].snapshot:
                    report = QuotaReport(
                        provider_id=provider_id,
                        snapshot=cached[1].snapshot,
                        error=str(exc),
                        error_kind=error_kind,
                        cached=True,
                    )
            except Exception:  # noqa: BLE001 - provider failures must not break /quota
                report = QuotaReport(
                    provider_id=provider_id,
                    error="Provider quota query failed",
                    error_kind="request",
                )
            else:
                report = QuotaReport(provider_id=provider_id, snapshot=snapshot)
                self._quota_cache[provider_id] = (monotonic(), report)
            return report
