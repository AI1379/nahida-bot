"""Pre-flight readiness checks.

Surfaces configuration problems that would leave the bot running but unable to
actually serve traffic (e.g. no usable LLM provider, channel tokens unresolved).
Used by the CLI ``start`` command and ``doctor`` to print loud, actionable
guidance instead of letting the bot come up silently broken.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nahida_bot.core.config import Settings


@dataclass(slots=True)
class ReadinessIssue:
    severity: str  # "error" | "warning"
    code: str
    message: str
    hint: str = ""


@dataclass(slots=True)
class ReadinessReport:
    issues: list[ReadinessIssue] = field(default_factory=list)

    @property
    def errors(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error")

    @property
    def warnings(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    @property
    def ok(self) -> bool:
        return self.errors == 0


def _has_usable_provider(
    settings: Settings,
    authenticated_provider_ids: frozenset[str] = frozenset(),
) -> tuple[bool, list[str], list[str]]:
    """Return (has_usable, usable_ids, skipped_ids).

    A provider is usable when it declares models and either has an api_key,
    has a credential saved by ``nahida-bot auth login``, or authenticates
    out-of-band (Codex OAuth). Mirrors Application's own gate.
    """
    usable: list[str] = []
    skipped: list[str] = []
    for pid, cfg in settings.providers.items():
        has_models = bool(cfg.models)
        needs_key = cfg.type != "codex"
        has_key = bool(cfg.api_key) or pid in authenticated_provider_ids
        if has_models and (has_key or not needs_key):
            usable.append(pid)
        else:
            skipped.append(pid)
    return bool(usable), usable, skipped


def check_readiness(
    settings: Settings,
    *,
    authenticated_provider_ids: frozenset[str] = frozenset(),
) -> ReadinessReport:
    """Run readiness checks against resolved settings.

    Distinguishes *errors* (bot cannot function) from *warnings* (degraded but
    usable). "No usable provider" is a warning rather than a hard error so that
    gateway-only / WebUI-only deployments remain possible.
    """
    report = ReadinessReport()

    has_usable, usable, skipped = _has_usable_provider(
        settings,
        authenticated_provider_ids,
    )
    if not settings.providers:
        report.issues.append(
            ReadinessIssue(
                "warning",
                "no_providers",
                "No LLM providers are configured.",
                "Run `nahida-bot bootstrap` to generate a minimal config, or "
                "edit config.yaml under `providers:`.",
            )
        )
    elif not has_usable:
        report.issues.append(
            ReadinessIssue(
                "warning",
                "no_usable_provider",
                f"None of the configured providers are usable (skipped: "
                f"{', '.join(skipped) or 'none'}).",
                "Every provider is missing credentials or models. Run "
                "`nahida-bot auth login <provider>`, check that .env values are "
                "loaded, and ensure each provider has at least one model.",
            )
        )
    elif skipped:
        report.issues.append(
            ReadinessIssue(
                "warning",
                "providers_skipped",
                f"Some providers will be skipped at startup: {', '.join(skipped)}",
                "Run `nahida-bot auth login <provider>`, add models, or remove "
                "the entry.",
            )
        )

    if usable and settings.default_provider and settings.default_provider not in usable:
        report.issues.append(
            ReadinessIssue(
                "error",
                "default_provider_unusable",
                f"default_provider '{settings.default_provider}' is not usable "
                f"(usable: {', '.join(usable)}).",
                "Authenticate it with `nahida-bot auth login`, or point "
                "default_provider at a provider that has credentials and models.",
            )
        )

    return report
