"""Interactive bootstrap / first-run configuration wizard.

Generates a minimal, working ``config.yaml`` + ``.env`` from a few prompts, so
new deployments don't have to edit the 500-line reference config. Reentrant:
when run against an existing config it only fills gaps instead of clobbering.

Usage::

    nahida-bot bootstrap                 # interactive, fresh or fix-missing
    nahida-bot bootstrap --fix-missing   # never overwrite existing values
    nahida-bot bootstrap --non-interactive  # write a skeleton from defaults/env
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import typer
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from nahida_bot.core.config import find_config_yaml, find_env_path, load_settings

console = Console()

bootstrap_app = typer.Typer(help="Interactive first-run configuration wizard")


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class ProviderTemplate:
    """A canned provider definition the wizard can stamp out."""

    label: str
    provider_type: str
    base_url: str
    key_env: str  # env var name that holds the api key
    key_label: str  # human label for the key
    models: list[dict[str, Any]]  # model entries with tags
    needs_key: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


# Curated presets. Keys mirror what config.yaml uses so existing users feel
# at home. base_url / models are sensible defaults; the api key always lives
# in .env and is referenced via ${VAR} interpolation.
_PROVIDER_PRESETS: dict[str, ProviderTemplate] = {
    "deepseek": ProviderTemplate(
        label="DeepSeek (official)",
        provider_type="deepseek",
        base_url="https://api.deepseek.com",
        key_env="DEEPSEEK_LLM_API_KEY",
        key_label="DeepSeek API key",
        models=[{"name": "deepseek-chat", "tags": ["primary"]}],
    ),
    "siliconflow": ProviderTemplate(
        label="SiliconFlow (OpenAI-compatible)",
        provider_type="openai-compatible",
        base_url="https://api.siliconflow.cn/v1",
        key_env="SILICONFLOW_LLM_API_KEY",
        key_label="SiliconFlow API key",
        models=[{"name": "Qwen/Qwen3.6-35B-A3B", "tags": ["primary", "vision"]}],
        extra={"merge_system_messages": True, "stream_responses": True},
    ),
    "openai": ProviderTemplate(
        label="OpenAI (Responses API)",
        provider_type="openai-responses",
        base_url="https://api.openai.com/v1",
        key_env="OPENAI_API_KEY",
        key_label="OpenAI API key",
        models=[{"name": "gpt-5.4", "tags": ["primary"]}],
        extra={"stream_responses": True},
    ),
    "anthropic": ProviderTemplate(
        label="Anthropic Claude",
        provider_type="anthropic",
        base_url="",
        key_env="ANTHROPIC_API_KEY",
        key_label="Anthropic API key",
        models=[{"name": "claude-sonnet-4-5", "tags": ["primary"]}],
    ),
    "glm": ProviderTemplate(
        label="GLM / ZhiPu",
        provider_type="glm",
        base_url="",
        key_env="GLM_API_KEY",
        key_label="GLM API key",
        models=[{"name": "glm-4-plus", "tags": ["primary"]}],
    ),
    "generic-openai": ProviderTemplate(
        label="Generic OpenAI-compatible (custom base_url)",
        provider_type="openai-compatible",
        base_url="",
        key_env="LLM_API_KEY",
        key_label="API key",
        models=[{"name": "gpt-3.5-turbo", "tags": ["primary"]}],
        extra={"stream_responses": True},
    ),
}


@dataclass(slots=True, frozen=True)
class ChannelTemplate:
    label: str
    key: str  # top-level config key
    fields: dict[str, str]  # config field -> ${ENV} reference
    secrets: dict[str, str]  # env var name -> human label
    extras: dict[str, Any] = field(default_factory=dict)


_CHANNEL_PRESETS: dict[str, ChannelTemplate] = {
    "telegram": ChannelTemplate(
        label="Telegram Bot",
        key="telegram",
        fields={"bot_token": "${TELEGRAM_BOT_TOKEN:}"},
        secrets={"TELEGRAM_BOT_TOKEN": "Telegram bot token (from @BotFather)"},
        extras={"enabled": True},
    ),
    "milky": ChannelTemplate(
        label="Milky QQ (Lagrange.Milky)",
        key="milky",
        fields={
            "base_url": "http://127.0.0.1:3000",
            "access_token": "${MILKY_ACCESS_TOKEN:}",
        },
        secrets={"MILKY_ACCESS_TOKEN": "Milky access token"},
        extras={"enabled": True, "group_trigger_mode": "mention"},
    ),
    "onebot": ChannelTemplate(
        label="OneBot v11 (NapCat/Lagrange/LLOneBot)",
        key="onebot",
        fields={
            "ws_url": "${ONEBOT_WS_URL:ws://127.0.0.1:3001}",
            "ws_access_token": "${ONEBOT_ACCESS_TOKEN:}",
        },
        secrets={
            "ONEBOT_ACCESS_TOKEN": "OneBot access token (optional)",
        },
        extras={"enabled": True, "protocol_version": "v11"},
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _read_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    header = (
        "# Nahida Bot configuration — generated by `nahida-bot bootstrap`.\n"
        "# Edit freely; secrets live in .env and are referenced via ${VAR}.\n"
        "# Run `nahida-bot config schema` for the full list of options.\n\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=78)
    path.write_text(header + body, encoding="utf-8")


def _write_env(path: Path, env: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Secrets for Nahida Bot — generated by `nahida-bot bootstrap`.",
        "# This file is gitignored; keep it out of version control.",
        "",
    ]
    for k, v in env.items():
        lines.append(f'{k}="{v}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _provider_yaml(preset: ProviderTemplate, provider_id: str) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "type": preset.provider_type,
        "api_key": f"${{{preset.key_env}:}}",
    }
    if preset.base_url:
        entry["base_url"] = preset.base_url
    entry.update(preset.extra)
    entry["models"] = list(preset.models)
    return {provider_id: entry}


def _pick_preset(
    prompt_msg: str,
    presets: dict[str, Any],
    *,
    default: str,
) -> str:
    options = list(presets.keys())
    console.print(Panel.fit(prompt_msg, border_style="cyan"))
    for idx, key in enumerate(options, 1):
        console.print(f"  [cyan]{idx}[/cyan]. {presets[key].label}")
    choice = Prompt.ask(
        "Choice",
        choices=[str(i) for i in range(1, len(options) + 1)],
        default=str(options.index(default) + 1),
    )
    return options[int(choice) - 1]


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------


@bootstrap_app.callback(invoke_without_command=True)
def bootstrap(
    ctx: typer.Context,
    config_yaml: str | None = typer.Option(
        None,
        "--config-yaml",
        "--config",
        "-c",
        help="Target config.yaml path (default: ./config.yaml)",
    ),
    env_path: str | None = typer.Option(
        None, "--env", help="Target .env path (default: ./.env)"
    ),
    fix_missing: bool = typer.Option(
        False,
        "--fix-missing",
        help="Only fill gaps; never overwrite values already present.",
    ),
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        help="Write a minimal skeleton without prompting (for scripts/Docker).",
    ),
) -> None:
    """Generate a minimal working config.yaml + .env."""
    target_yaml = Path(config_yaml or find_config_yaml(None) or "config.yaml")
    target_env = Path(env_path or find_env_path(None) or ".env")

    existing_yaml = _read_yaml(target_yaml)
    existing_env = _read_env(target_env)

    has_config = bool(existing_yaml)
    if has_config:
        console.print(
            f"[yellow]Found existing {target_yaml}.[/yellow] "
            + ("Preserving existing values (--fix-missing)." if fix_missing else "")
        )

    # --- provider selection ---
    providers_section: dict[str, Any] = dict(existing_yaml.get("providers") or {})
    new_env: dict[str, str] = {}
    default_provider = existing_yaml.get("default_provider", "")

    if non_interactive:
        if not providers_section:
            preset = _PROVIDER_PRESETS[
                os.environ.get("NAHIDA_BOOTSTRAP_PROVIDER", "deepseek")
            ]
            pid = os.environ.get("NAHIDA_BOOTSTRAP_PROVIDER_ID", "main")
            providers_section.update(_provider_yaml(preset, pid))
            default_provider = pid
            new_env[preset.key_env] = os.environ.get(preset.key_env, "")
    else:
        if not providers_section or Confirm.ask(
            "Configure an LLM provider?", default=True
        ):
            preset_key = _pick_preset(
                "Choose an LLM provider preset:",
                _PROVIDER_PRESETS,
                default="deepseek",
            )
            preset = _PROVIDER_PRESETS[preset_key]
            pid = Prompt.ask("Provider id (the key in config)", default=preset_key)
            if preset.base_url and not fix_missing:
                base = Prompt.ask("Base URL", default=preset.base_url)
                preset_b: ProviderTemplate = ProviderTemplate(
                    label=preset.label,
                    provider_type=preset.provider_type,
                    base_url=base,
                    key_env=preset.key_env,
                    key_label=preset.key_label,
                    models=preset.models,
                    needs_key=preset.needs_key,
                    extra=preset.extra,
                )
            else:
                preset_b = preset
            key_value = ""
            if preset.needs_key:
                key_value = Prompt.ask(preset.key_label, password=True, default="")
            if pid not in providers_section or not fix_missing:
                providers_section.update(_provider_yaml(preset_b, pid))
            if preset.needs_key:
                new_env[preset.key_env] = key_value
            default_provider = default_provider or pid

    # --- channel selection ---
    channels_section: dict[str, dict[str, Any]] = {}
    for k, v in existing_yaml.items():
        if k in _CHANNEL_PRESETS and isinstance(v, dict):
            channels_section[k] = v

    if non_interactive:
        desired = [
            c.strip()
            for c in os.environ.get("NAHIDA_BOOTSTRAP_CHANNELS", "").split(",")
            if c.strip()
        ]
        for ch_key in desired:
            if ch_key in _CHANNEL_PRESETS and (
                ch_key not in channels_section or not fix_missing
            ):
                tpl = _CHANNEL_PRESETS[ch_key]
                channels_section[ch_key] = {**tpl.extras, **tpl.fields}
                for env_name in tpl.secrets:
                    new_env[env_name] = os.environ.get(env_name, "")
    else:
        while True:
            remaining = {
                k: v for k, v in _CHANNEL_PRESETS.items() if k not in channels_section
            }
            if not remaining:
                console.print("[dim]All known channels are configured.[/dim]")
                break
            if not Confirm.ask(
                "Configure a messaging channel?", default=not channels_section
            ):
                break
            ch_key = _pick_preset(
                "Choose a channel to add:",
                remaining,
                default=next(iter(remaining)),
            )
            tpl = _CHANNEL_PRESETS[ch_key]
            channel_entry: dict[str, Any] = dict(tpl.extras)
            for env_name, label in tpl.secrets.items():
                val = Prompt.ask(label, password=True, default="")
                new_env[env_name] = val
            channel_entry.update(tpl.fields)
            channels_section[ch_key] = channel_entry

    # --- assemble final config ---
    merged: dict[str, Any] = dict(existing_yaml)
    merged["providers"] = providers_section
    if default_provider:
        merged["default_provider"] = default_provider
    for ch_key, ch_val in channels_section.items():
        merged[ch_key] = ch_val
    # Ensure the bare-minimum framework fields exist.
    merged.setdefault("app_name", "Nahida Bot")
    merged.setdefault("db_path", "./data/nahida.db")
    merged.setdefault("workspace_base_dir", "./data/workspace")
    merged.setdefault("plugin_paths", ["./plugins"])

    # --- validate before writing ---
    tmp_yaml = target_yaml.with_suffix(".tmp.bootstrap")
    try:
        _write_yaml(tmp_yaml, merged)
        load_settings(
            config_yaml=str(tmp_yaml),
            env_path=str(target_env) if target_env.is_file() else None,
        )
        tmp_yaml.unlink(missing_ok=True)
    except Exception as exc:
        console.print(f"[bold red]Generated config failed validation:[/bold red] {exc}")
        tmp_yaml.unlink(missing_ok=True)
        raise typer.Exit(1)

    _write_yaml(target_yaml, merged)
    # Merge new secrets into existing env (preserve untouched entries, keep
    # empty placeholders so the user can see what to fill in).
    final_env = {**existing_env, **new_env}
    if final_env or target_env.is_file():
        _write_env(target_env, final_env)

    console.print(
        f"\n[bold green]Done.[/bold green]\n"
        f"  config: [cyan]{target_yaml}[/cyan]\n"
        f"  env:    [cyan]{target_env}[/cyan]\n"
    )
    provider_count = len(merged.get("providers", {}))
    channel_count = sum(
        1 for k in merged if k in _CHANNEL_PRESETS and merged[k].get("enabled")
    )
    console.print(f"  providers: {provider_count}  channels: {channel_count}\n")
    if not provider_count:
        console.print(
            "[yellow]No provider configured — run `nahida-bot bootstrap` again "
            "or edit config.yaml before starting the bot.[/yellow]"
        )
    console.print(
        "Next: edit [cyan].env[/cyan] to fill in secrets, then "
        "[cyan]nahida-bot doctor[/cyan] to verify, and "
        "[cyan]nahida-bot start[/cyan] to launch."
    )
