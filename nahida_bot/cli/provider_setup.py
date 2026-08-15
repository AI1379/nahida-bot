"""Shared interactive provider/channel selection for CLI commands.

Thin UI composition over :mod:`nahida_bot.agent.providers.catalog` (what a
provider entry looks like) — the orchestration differences between
``bootstrap`` and ``auth login`` stay in their own modules.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from nahida_bot.agent.providers.catalog import (
    PROVIDER_PRESETS,
    ProviderTemplate,
    preset_for_type,
    preset_with_base_url,
)


def pick_labelled(
    options: dict[str, Any],
    prompt_msg: str,
    *,
    default: str,
    console: Console,
) -> str:
    """Numbered menu over a ``{key: obj-with-.label}`` mapping; returns key."""
    keys = list(options.keys())
    console.print(Panel.fit(prompt_msg, border_style="cyan"))
    for idx, key in enumerate(keys, 1):
        console.print(f"  [cyan]{idx}[/cyan]. {options[key].label}")
    choice = Prompt.ask(
        "Choice",
        choices=[str(i) for i in range(1, len(keys) + 1)],
        default=str(keys.index(default) + 1),
    )
    return keys[int(choice) - 1]


def pick_provider_preset(
    prompt_msg: str,
    *,
    default: str,
    console: Console,
) -> str:
    """Menu over :data:`PROVIDER_PRESETS`; returns the preset slug."""
    return pick_labelled(PROVIDER_PRESETS, prompt_msg, default=default, console=console)


def prompt_provider_template(
    provider_id: str,
    *,
    console: Console,
    suggested_type: str | None = None,
    confirm_suggested: bool = True,
) -> ProviderTemplate:
    """Interactively build the provider template for a new ``<id>`` entry.

    When *suggested_type* names a known provider type (``auth login codex``),
    the type menu is skipped — optionally without an extra confirmation for
    the fast path where the requested id itself names the type.
    """
    preset: ProviderTemplate | None = None
    if suggested_type:
        preset = preset_for_type(suggested_type)
        if preset is None:
            # Runtime/plugin-registered type without a curated preset.
            preset = ProviderTemplate(
                label=f"{suggested_type} (custom)",
                provider_type=suggested_type,
                key_env="LLM_API_KEY",
                key_label="API key",
                base_url_required=True,
            )
        if not confirm_suggested or Confirm.ask(
            f"Use provider type '{suggested_type}'?", default=True
        ):
            chosen = preset
        else:
            chosen = None
    else:
        chosen = None

    if chosen is None:
        slug = pick_provider_preset(
            f"Choose the provider type for '{provider_id}':",
            default="generic-openai",
            console=console,
        )
        chosen = PROVIDER_PRESETS[slug]

    if chosen.base_url_required:
        base_url = Prompt.ask("Base URL").strip()
    elif chosen.base_url:
        base_url = Prompt.ask("Base URL", default=chosen.base_url).strip()
    else:
        base_url = ""
    if base_url and base_url != chosen.base_url:
        chosen = preset_with_base_url(chosen, base_url)

    preset_names = [str(model.get("name", "")) for model in chosen.models]
    raw = Prompt.ask(
        "Model names (comma-separated)",
        default=", ".join(preset_names),
    )
    names = [part.strip() for part in raw.split(",") if part.strip()]
    if names and names != preset_names:
        # Custom model lists lose curated tags; keep 'primary' on the first
        # entry so the capability checklist and model routing still work.
        models: list[dict[str, Any]] = []
        for position, name in enumerate(names):
            entry: dict[str, Any] = {"name": name}
            if position == 0:
                entry["tags"] = ["primary"]
            models.append(entry)
        chosen = replace(chosen, models=models)

    return chosen
