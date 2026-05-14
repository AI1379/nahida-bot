"""Tag-aware model routing — resolves tasks to (provider, model) pairs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import structlog

if TYPE_CHECKING:
    from nahida_bot.agent.providers.manager import ProviderManager, ProviderSlot

logger = structlog.get_logger(__name__)


@dataclass(slots=True, frozen=True)
class RoutedModel:
    """Result of a model routing resolution."""

    slot: ProviderSlot
    model: str | None
    reason: str


TaskFallback = Literal["session", "default", "disabled", "none"]


class ModelRouter:
    """Resolves model specs to ``(ProviderSlot, model_name)`` pairs.

    A *spec* can be:

    * A concrete ``provider_id/model_name`` or bare model name — resolved
      via :pyclass:`ProviderManager`.
    * A **tag** like ``"embedding"`` or ``"cheap"`` — resolved by scanning
      model tags across all provider slots.

    Task-level routing is intentionally code-level, not user-configured:
    explicit spec → task default spec → task fallback policy.
    """

    def __init__(
        self,
        provider_manager: ProviderManager,
    ) -> None:
        self._pm = provider_manager

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

    def resolve_for_task(
        self,
        task: str,
        *,
        explicit: str = "",
        default_spec: str = "",
        fallback: TaskFallback = "disabled",
    ) -> RoutedModel | None:
        """Resolve model for a named task.

        Priority: *explicit* spec → task default spec → fallback policy.

        When *fallback* is ``"session"`` this method returns ``None`` so the
        caller can apply its own session-level resolution.
        """
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
            logger.warning(
                "model_router.explicit_missed",
                task=task,
                explicit=explicit,
            )

        if default_spec:
            match = self.resolve(default_spec)
            if match is not None:
                logger.debug(
                    "model_router.task_resolved",
                    task=task,
                    reason=f"default:{default_spec}",
                    slot=match.slot.id,
                    model=match.model,
                )
                return match
            logger.warning(
                "model_router.default_spec_missed",
                task=task,
                default_spec=default_spec,
            )

        return self._apply_fallback(task, fallback)

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

    def _apply_fallback(self, task: str, fallback: TaskFallback) -> RoutedModel | None:
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
