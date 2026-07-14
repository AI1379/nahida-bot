"""Config validation service.

Shared by CLI and WebUI. ValidationIssue and ValidationReport are the public
API; validate_settings() is the main entry point.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import ValidationError

from nahida_bot.core.config import Settings


@dataclass(slots=True)
class ValidationIssue:
    severity: str  # "error" | "warning"
    path: str
    message: str


@dataclass(slots=True)
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error")

    @property
    def warnings(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    @property
    def ok(self) -> bool:
        return self.errors == 0


def _legacy_model_spec(*, provider_id: str = "", model: str = "") -> str:
    provider_id = provider_id.strip()
    model = model.strip()
    if provider_id and model:
        if model.startswith(f"{provider_id}/"):
            return model
        return f"{provider_id}/{model}"
    return model


def _iter_config_models(settings: Settings) -> list[tuple[str, str, list[str]]]:
    models: list[tuple[str, str, list[str]]] = []
    for provider_id, entry in settings.providers.items():
        for model_entry in entry.models:
            if isinstance(model_entry, str):
                if model_entry:
                    models.append((provider_id, model_entry, []))
                continue
            name = getattr(model_entry, "name", "")
            if name:
                models.append((provider_id, name, list(model_entry.tags)))
    return models


def _provider_has_model(settings: Settings, provider_id: str, model: str) -> bool:
    return any(
        pid == provider_id and model_name == model
        for pid, model_name, _tags in _iter_config_models(settings)
    )


def _model_spec_resolves(settings: Settings, spec: str) -> bool:
    """Return whether a model spec can resolve by provider/model, bare name, or tag."""
    spec = spec.strip()
    if not spec:
        return False

    if "/" in spec:
        provider_id, _, bare_model = spec.partition("/")
        if provider_id in settings.providers:
            return _provider_has_model(settings, provider_id, bare_model)

    for _provider_id, model_name, tags in _iter_config_models(settings):
        if spec == model_name or spec in tags:
            return True

    return spec == "primary" and any(
        entry.models for entry in settings.providers.values()
    )


def _enabled_plugin_config(
    report: ValidationReport,
    plugin_id: str,
    raw: dict,
    *,
    default: bool,
) -> dict | None:
    """Return business config when a framework-managed plugin is enabled."""
    enabled = raw.get("enabled", default)
    if not isinstance(enabled, bool):
        report.issues.append(
            ValidationIssue(
                "error",
                f"{plugin_id}.enabled",
                "Plugin enabled must be a boolean",
            )
        )
        return None
    if not enabled:
        return None
    business_config = dict(raw)
    business_config.pop("enabled", None)
    return business_config


def _add_unresolved_model_issue(
    report: ValidationReport,
    path: str,
    spec: str,
) -> None:
    report.issues.append(
        ValidationIssue(
            "error",
            path,
            f"Model spec '{spec}' does not match any provider/model, model name, or tag",
        )
    )


def _add_pydantic_issues(
    report: ValidationReport,
    *,
    prefix: str,
    exc: ValidationError,
) -> None:
    for error in exc.errors():
        loc = error.get("loc", ())
        suffix = ".".join(str(part) for part in loc if part != "__root__")
        report.issues.append(
            ValidationIssue(
                "error",
                f"{prefix}.{suffix}" if suffix else prefix,
                str(error.get("msg", "Invalid configuration")),
            )
        )


def validate_settings(settings: Settings) -> ValidationReport:
    """Validate a Settings object for common issues.

    Returns a ValidationReport with errors (blocking) and warnings (advisory).
    """
    report = ValidationReport()

    provider_ids = list(settings.providers.keys())

    if not provider_ids:
        report.issues.append(
            ValidationIssue("warning", "providers", "No LLM providers configured")
        )
    else:
        if settings.default_provider and settings.default_provider not in provider_ids:
            report.issues.append(
                ValidationIssue(
                    "error",
                    "default_provider",
                    f"'{settings.default_provider}' not found in providers: "
                    f"{', '.join(provider_ids)}",
                )
            )

        for pid, entry in settings.providers.items():
            if not entry.api_key:
                report.issues.append(
                    ValidationIssue(
                        "warning",
                        f"providers.{pid}.api_key",
                        f"Provider '{pid}' has no api_key set",
                    )
                )
            if not entry.models:
                report.issues.append(
                    ValidationIssue(
                        "warning",
                        f"providers.{pid}.models",
                        f"Provider '{pid}' has no models configured",
                    )
                )

    # --- multimodal ---
    mm = settings.multimodal
    if mm.image_fallback_mode != "off":
        fallback_spec = _legacy_model_spec(
            provider_id=mm.image_fallback_provider,
            model=mm.image_fallback_model,
        )
        if fallback_spec:
            if not _model_spec_resolves(settings, fallback_spec):
                _add_unresolved_model_issue(
                    report,
                    "multimodal.image_fallback_model",
                    fallback_spec,
                )
        elif mm.image_fallback_provider:
            if mm.image_fallback_provider not in settings.providers:
                report.issues.append(
                    ValidationIssue(
                        "error",
                        "multimodal.image_fallback_provider",
                        f"Provider '{mm.image_fallback_provider}' is not configured",
                    )
                )
        elif not _model_spec_resolves(settings, "vision"):
            report.issues.append(
                ValidationIssue(
                    "warning",
                    "multimodal.image_fallback_model",
                    "Image fallback is enabled but no fallback model/provider is "
                    "configured and no model has the 'vision' tag",
                )
            )

    # --- memory ---
    mem = settings.memory
    if mem.enabled:
        emb = mem.embedding
        if emb.enabled:
            embedding_spec = _legacy_model_spec(
                provider_id=emb.provider_id,
                model=emb.model,
            )
            if embedding_spec:
                if not _model_spec_resolves(settings, embedding_spec):
                    _add_unresolved_model_issue(
                        report,
                        "memory.embedding.model",
                        embedding_spec,
                    )
            elif emb.provider_id:
                report.issues.append(
                    ValidationIssue(
                        "warning",
                        "memory.embedding.provider_id",
                        "memory.embedding.provider_id without memory.embedding.model "
                        "is ignored; use memory.embedding.model: provider/model",
                    )
                )
                if not _model_spec_resolves(settings, "embedding"):
                    report.issues.append(
                        ValidationIssue(
                            "warning",
                            "memory.embedding",
                            "Embedding is enabled but no model has the 'embedding' tag",
                        )
                    )
            elif not _model_spec_resolves(settings, "embedding"):
                report.issues.append(
                    ValidationIssue(
                        "warning",
                        "memory.embedding",
                        "Embedding is enabled but no model has the 'embedding' tag, "
                        "and memory.embedding.model is not set",
                    )
                )

        ret = mem.retrieval
        if ret.vector_enabled and ret.vector_backend == "sqlite-vec":
            try:
                import sqlite_vec  # type: ignore[import-untyped]  # noqa: F401
            except ImportError:
                report.issues.append(
                    ValidationIssue(
                        "error",
                        "memory.retrieval.vector_backend",
                        "sqlite-vec backend requires 'pip install sqlite-vec'",
                    )
                )
            if not emb.enabled:
                report.issues.append(
                    ValidationIssue(
                        "warning",
                        "memory.embedding.enabled",
                        "sqlite-vec vector retrieval is enabled but memory embedding "
                        "is disabled",
                    )
                )
            elif emb.dimensions <= 0:
                report.issues.append(
                    ValidationIssue(
                        "warning",
                        "memory.embedding.dimensions",
                        "sqlite-vec vector retrieval will need to probe embedding "
                        "dimensions at startup; configure dimensions for deterministic setup",
                    )
                )

    # --- scheduler ---
    scheduler = settings.scheduler
    if scheduler.memory_dreaming_enabled:
        dreaming_spec = _legacy_model_spec(
            provider_id=scheduler.memory_dreaming_provider_id,
            model=scheduler.memory_dreaming_model,
        )
        if dreaming_spec and not _model_spec_resolves(settings, dreaming_spec):
            _add_unresolved_model_issue(
                report,
                "scheduler.memory_dreaming_model",
                dreaming_spec,
            )
        elif (
            scheduler.memory_dreaming_provider_id
            and not scheduler.memory_dreaming_model
        ):
            report.issues.append(
                ValidationIssue(
                    "warning",
                    "scheduler.memory_dreaming_provider_id",
                    "memory_dreaming_provider_id without memory_dreaming_model is "
                    "ignored; use memory_dreaming_model: provider/model",
                )
            )

    # --- channels ---
    extra = settings.model_extra or {}
    if "telegram" in extra and isinstance(extra["telegram"], dict):
        from nahida_bot.channels.telegram.config import parse_telegram_config

        telegram_config = _enabled_plugin_config(
            report, "telegram", extra["telegram"], default=False
        )
        try:
            telegram = (
                parse_telegram_config(telegram_config)
                if telegram_config is not None
                else None
            )
        except ValidationError as exc:
            _add_pydantic_issues(report, prefix="telegram", exc=exc)
        else:
            if telegram is not None and not telegram.bot_token:
                report.issues.append(
                    ValidationIssue(
                        "warning",
                        "telegram.bot_token",
                        "Telegram is configured but bot_token is not set",
                    )
                )

    if "milky" in extra and isinstance(extra["milky"], dict):
        from nahida_bot.channels.milky.config import parse_milky_config

        milky_config = _enabled_plugin_config(
            report, "milky", extra["milky"], default=False
        )
        try:
            milky = (
                parse_milky_config(milky_config) if milky_config is not None else None
            )
        except ValidationError as exc:
            _add_pydantic_issues(report, prefix="milky", exc=exc)
        else:
            if milky is not None and not milky.access_token:
                report.issues.append(
                    ValidationIssue(
                        "warning",
                        "milky.access_token",
                        "Milky is configured but access_token is not set",
                    )
                )

    if "onebot" in extra and isinstance(extra["onebot"], dict):
        from nahida_bot.channels.onebot.config import parse_onebot_config

        onebot_config = _enabled_plugin_config(
            report, "onebot", extra["onebot"], default=False
        )
        try:
            if onebot_config is not None:
                parse_onebot_config(onebot_config)
        except ValidationError as exc:
            _add_pydantic_issues(report, prefix="onebot", exc=exc)

    return report
