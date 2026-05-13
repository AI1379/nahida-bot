"""Tag-aware model routing — resolves tasks to (provider, model) pairs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from nahida_bot.agent.providers.manager import ProviderManager, ProviderSlot
    from nahida_bot.core.config import ModelRoutingConfig

logger = structlog.get_logger(__name__)


@dataclass(slots=True, frozen=True)
class RoutedModel:
    """Result of a model routing resolution."""

    slot: ProviderSlot
    model: str | None
    reason: str


class ModelRouter:
    """Resolves model specs to ``(ProviderSlot, model_name)`` pairs.

    A *spec* can be:

    * A concrete ``provider_id/model_name`` or bare model name — resolved
      via :pyclass:`ProviderManager`.
    * A **tag** like ``"embedding"`` or ``"cheap"`` — resolved by scanning
      model tags across all provider slots.

    Task-level routing (``resolve_for_task``) adds a fallback chain:
    explicit override → ``prefer_tags`` → ``fallback`` policy.
    """

    def __init__(
        self,
        provider_manager: ProviderManager,
        routing_config: ModelRoutingConfig,
    ) -> None:
        self._pm = provider_manager
        self._config = routing_config

    # ── Public API ─────────────────────────────────────────

    def resolve(self, spec: str) -> RoutedModel | None:
        """Resolve *spec* to a ``(slot, model)`` pair.

        Resolution order: concrete provider/model → bare model name → tag.
        Returns ``None`` when nothing matches.
        """
        spec = spec.strip()
        if not spec:
            return None

        # 1. Try as a concrete provider/model or bare model name
        resolved = self._pm.resolve_model_selection(spec)
        if resolved is not None:
            slot, model = resolved
            return RoutedModel(slot=slot, model=model, reason="explicit")

        # 2. Try as a tag
        tag_match = self._resolve_by_tag(spec)
        if tag_match is not None:
            return tag_match

        logger.debug(
            "model_router.resolve_missed",
            spec=spec,
        )
        return None

    def resolve_for_task(self, task: str, *, explicit: str = "") -> RoutedModel | None:
        """Resolve model for a named task.

        Priority: *explicit* override → task ``prefer_tags`` → fallback policy.

        When *fallback* is ``"session"`` this method returns ``None`` so the
        caller can apply its own session-level resolution.
        """
        # 1. Explicit override (accepts concrete or tag form)
        if explicit:
            result = self.resolve(explicit)
            if result is not None:
                logger.debug(
                    "model_router.task_resolved",
                    task=task,
                    reason="explicit_override",
                    slot=result.slot.id,
                    model=result.model,
                )
                return result

        # 2. Look up task config
        entry = self._get_task_entry(task)
        if entry is None:
            logger.debug("model_router.no_task_config", task=task)
            return self._apply_fallback(task, "session")

        # 3. Try prefer_tags in order
        for tag in entry.prefer_tags:
            match = self._resolve_by_tag(tag)
            if match is not None:
                logger.debug(
                    "model_router.task_resolved",
                    task=task,
                    reason=f"tag:{tag}",
                    slot=match.slot.id,
                    model=match.model,
                )
                return match

        # 4. Apply fallback policy
        return self._apply_fallback(task, entry.fallback)

    # ── Internals ──────────────────────────────────────────

    def _resolve_by_tag(self, tag: str) -> RoutedModel | None:
        """Find first model matching *tag* across all provider slots.

        The tag ``"primary"`` implicitly matches each provider's default model
        (i.e. the first model in that provider's config).
        """
        for slot in self._pm._slots.values():
            # Check explicit tags first
            for model_name, tags in slot.tags_by_model.items():
                if tag in tags:
                    return RoutedModel(
                        slot=slot,
                        model=model_name,
                        reason=f"tag:{tag}",
                    )
            # "primary" implicitly matches the default model
            if tag == "primary" and slot.default_model:
                if slot.default_model not in slot.tags_by_model:
                    return RoutedModel(
                        slot=slot,
                        model=slot.default_model,
                        reason="tag:primary",
                    )
        return None

    def _apply_fallback(self, task: str, fallback: str) -> RoutedModel | None:
        """Apply the fallback policy for a task."""
        if fallback == "default":
            slot = self._pm.default
            if slot is not None:
                logger.debug(
                    "model_router.task_fallback",
                    task=task,
                    fallback="default",
                    slot=slot.id,
                )
                return RoutedModel(slot=slot, model=None, reason="fallback:default")
            return None

        if fallback in ("session", "none", "disabled"):
            logger.debug(
                "model_router.task_fallback",
                task=task,
                fallback=fallback,
            )
            return None

        return None

    def _get_task_entry(self, task: str):
        """Retrieve the routing entry for a task name."""
        # Try direct attribute first (defined fields)
        entry = getattr(self._config, task, None)
        if entry is not None:
            return entry
        # Try extra fields (Pydantic stores them via extra="allow")
        extra = getattr(self._config, "model_extra", None) or {}
        return extra.get(task)
