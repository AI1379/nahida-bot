"""Unified provider credential and OAuth management commands."""

from __future__ import annotations

import asyncio
import difflib
import time
from pathlib import Path

import httpx
import typer
import yaml
from rich.console import Console
from rich.prompt import Confirm
from rich.table import Table

from nahida_bot.agent.providers.catalog import (
    ProviderTemplate,
    is_known_provider_type,
    preset_for_type,
)
from nahida_bot.auth import (
    DEVICE_VERIFICATION_URL,
    poll_device_challenge,
    request_device_challenge,
    resolve_client_id,
    resolve_originator,
    to_codex_token,
)
from nahida_bot.cli.provider_setup import prompt_provider_template
from nahida_bot.core.config import Settings, find_config_yaml, load_settings_auto
from nahida_bot.core.yaml_edit import YamlEditError, upsert_entry
from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.db.repositories.sqlite_codex_token_repo import (
    SQLiteCodexTokenRepository,
)
from nahida_bot.db.repositories.sqlite_provider_credential_repo import (
    ProviderCredential,
    SQLiteProviderCredentialRepository,
)

console = Console()
auth_app = typer.Typer(help="Provider authentication and credential management")


def _provider_snippet(provider_id: str, template: ProviderTemplate) -> str:
    """Render the YAML block a user would add manually for *provider_id*."""
    body = yaml.safe_dump(
        template.render_entry(), sort_keys=False, allow_unicode=True
    ).rstrip("\n")
    indented = "\n".join(f"    {line}" for line in body.splitlines())
    return f"providers:\n  {provider_id}:\n{indented}"


def _provision_provider(
    settings: Settings,
    requested: str,
    config: str | None,
) -> tuple[str, Settings]:
    """Handle ``auth login <id>`` for an id missing from the config.

    Offers typo correction against configured ids, then an interactive setup
    flow that writes a minimal ``providers.<id>`` entry into the config file
    (comment-preserving) before the caller continues with the credential step.
    Degrades to an actionable snippet when stdin is unavailable.
    """
    configured = list(settings.providers)
    console.print(
        f"[yellow]Provider '{requested}' is not configured.[/yellow]"
        + (f" Configured: {', '.join(configured)}" if configured else "")
    )

    close = difflib.get_close_matches(requested, configured, n=1, cutoff=0.6)
    if close:
        if Confirm.ask(f"Did you mean '{close[0]}'?", default=True):
            return close[0], settings
        console.print(f"[dim]Continuing with a new provider '{requested}'.[/dim]")

    config_path = find_config_yaml(config)
    # Best-effort template for static snippet messages (no config file / EOF):
    # prefer the type the requested id itself names.
    fallback_template = preset_for_type(requested) or ProviderTemplate(
        label="",
        provider_type="openai-compatible",
        key_env="LLM_API_KEY",
        models=[{"name": "gpt-3.5-turbo", "tags": ["primary"]}],
    )
    if not config_path:
        console.print(
            "[red]No config file found to write the provider entry into.[/red]\n"
            "Run `nahida-bot bootstrap` first, or add this block manually:\n"
            + _provider_snippet(requested, fallback_template)
        )
        raise typer.Exit(1)

    try:
        if not Confirm.ask(f"Create new provider '{requested}' now?", default=True):
            raise typer.Exit(1)
        id_names_type = is_known_provider_type(requested)
        template = prompt_provider_template(
            requested,
            console=console,
            suggested_type=requested if id_names_type else None,
            # The requested id already names the type — no second confirmation.
            confirm_suggested=not id_names_type,
        )
        backup_path = upsert_entry(
            Path(config_path), "providers", requested, template.render_entry()
        )
        if backup_path:
            console.print(f"[dim]Backup written: {backup_path}[/dim]")
    except EOFError:
        console.print(
            "[red]No interactive terminal available.[/red] "
            "Add this block to your config file and retry:\n"
            + _provider_snippet(requested, fallback_template)
        )
        raise typer.Exit(1) from None
    except YamlEditError as exc:
        console.print(f"[red]Could not update {config_path}: {exc}[/red]")
        raise typer.Exit(1) from None

    settings = load_settings_auto(config)
    if requested not in settings.providers:
        console.print(
            f"[red]Provider '{requested}' still missing after config update — "
            "the generated entry failed validation. See the message above.[/red]"
        )
        raise typer.Exit(1)
    return requested, settings


def _select_provider(settings: Settings, requested: str | None) -> str:
    provider_ids = list(settings.providers)
    if requested:
        if requested not in settings.providers:
            console.print(
                f"[red]Provider '{requested}' is not configured.[/red] "
                f"Available: {', '.join(provider_ids) or '(none)'}\n"
                "Run `nahida-bot auth login <id>` to set it up interactively."
            )
            raise typer.Exit(1)
        return requested
    if not provider_ids:
        console.print("[red]No providers are configured.[/red]")
        raise typer.Exit(1)
    if len(provider_ids) == 1:
        return provider_ids[0]
    console.print("Configured providers: " + ", ".join(provider_ids))
    selected = typer.prompt("Provider id").strip()
    if selected not in settings.providers:
        console.print(f"[red]Provider '{selected}' is not configured.[/red]")
        raise typer.Exit(1)
    return selected


async def _run_codex_login(
    provider_id: str,
    settings: Settings,
) -> int:
    engine = DatabaseEngine(settings.db_path)
    await engine.initialize()
    try:
        repo = SQLiteCodexTokenRepository(engine)
        client_id = resolve_client_id()
        originator = resolve_originator()
        console.print(
            f"[bold cyan]Starting ChatGPT Codex device login[/bold cyan]\n"
            f"  provider: [cyan]{provider_id}[/cyan]\n"
            f"  originator: [cyan]{originator}[/cyan]\n"
            f"  client_id: [dim]{client_id}[/dim]"
        )
        async with httpx.AsyncClient() as client:
            challenge = await request_device_challenge(client)
            console.print(
                "\n[bold yellow]Open this URL in any browser:[/bold yellow]\n"
                f"  [underline]{DEVICE_VERIFICATION_URL}[/underline]\n"
                f"\n[bold yellow]Enter the code:[/bold yellow]\n"
                f"  [bold]{challenge.user_code}[/bold]\n"
                f"\n[dim]Waiting for approval "
                f"(polling every {challenge.interval_seconds:.0f}s)…[/dim]"
            )

            async def _heartbeat() -> None:
                console.print("[dim]·[/dim]", end=" ", soft_wrap=True)

            tokens = await poll_device_challenge(
                client,
                challenge,
                client_id=client_id,
                on_pending=_heartbeat,
            )

        codex_token = to_codex_token(tokens)
        await repo.upsert(provider_id, codex_token)
        console.print(
            f"\n[bold green]✓ Login successful[/bold green]\n"
            f"  provider: [cyan]{provider_id}[/cyan]\n"
            f"  account_id: [cyan]{codex_token.account_id or '(unknown)'}[/cyan]\n"
            f"  expires_at: epoch {codex_token.expires_at}"
        )
        return 0
    finally:
        await engine.close()


async def _store_api_key(
    provider_id: str,
    api_key: str,
    settings: Settings,
) -> None:
    engine = DatabaseEngine(settings.db_path)
    await engine.initialize()
    try:
        repo = SQLiteProviderCredentialRepository(engine)
        await repo.upsert(
            ProviderCredential(
                provider_id=provider_id,
                auth_method="api_key",
                secret=api_key,
            )
        )
    finally:
        await engine.close()


async def _remove_credentials(
    provider_id: str,
    settings: Settings,
) -> tuple[bool, bool]:
    engine = DatabaseEngine(settings.db_path)
    await engine.initialize()
    try:
        api_key_removed = await SQLiteProviderCredentialRepository(engine).delete(
            provider_id
        )
        codex_removed = await SQLiteCodexTokenRepository(engine).delete(provider_id)
        return api_key_removed, codex_removed
    finally:
        await engine.close()


async def _credential_rows(settings: Settings) -> list[tuple[str, str, str, str]]:
    engine = DatabaseEngine(settings.db_path)
    await engine.initialize()
    try:
        key_repo = SQLiteProviderCredentialRepository(engine)
        codex_repo = SQLiteCodexTokenRepository(engine)
        stored_keys = {item.provider_id: item for item in await key_repo.list_all()}
        codex_ids = set(await codex_repo.list_provider_ids())
        provider_ids = sorted(set(settings.providers) | set(stored_keys) | codex_ids)
        rows: list[tuple[str, str, str, str]] = []
        now = time.time()
        for provider_id in provider_ids:
            config = settings.providers.get(provider_id)
            provider_type = config.type if config is not None else "(not configured)"
            if provider_id in codex_ids:
                token = await codex_repo.get(provider_id)
                if token is None:
                    continue
                status = (
                    "valid"
                    if token.access_token and token.expires_at > now
                    else "refresh available"
                    if token.refresh_token
                    else "invalid"
                )
                rows.append((provider_id, provider_type, "codex_oauth", status))
            elif provider_id in stored_keys:
                rows.append((provider_id, provider_type, "api_key (stored)", "ready"))
            elif config is not None and config.api_key:
                rows.append((provider_id, provider_type, "api_key (config)", "ready"))
            else:
                method = "codex_oauth" if provider_type == "codex" else "api_key"
                rows.append((provider_id, provider_type, method, "missing"))
        return rows
    finally:
        await engine.close()


@auth_app.command(name="login")
def login(
    provider: str | None = typer.Argument(
        None,
        help="Provider id; offers interactive setup when not configured yet",
    ),
    config: str | None = typer.Option(
        None,
        "--config-yaml",
        "--config",
        "-c",
        help="Path to YAML configuration file",
    ),
) -> None:
    """Log in to a provider using OAuth or a hidden API-key prompt.

    When the provider id is missing from the config, offers typo correction
    and an interactive setup flow that writes a minimal ``providers.<id>``
    entry (codex needs no api key — login goes straight to device OAuth).
    """
    settings = load_settings_auto(config)
    if provider and provider not in settings.providers:
        provider, settings = _provision_provider(settings, provider, config)
    provider_id = _select_provider(settings, provider)
    provider_config = settings.providers[provider_id]
    if provider_config.type == "codex":
        rc = asyncio.run(_run_codex_login(provider_id, settings))
        if rc != 0:
            raise typer.Exit(rc)
        return

    api_key = typer.prompt(f"API key for {provider_id}", hide_input=True).strip()
    if not api_key:
        console.print("[red]API key must not be empty.[/red]")
        raise typer.Exit(1)
    asyncio.run(_store_api_key(provider_id, api_key, settings))
    console.print(
        f"[green]✓ Stored API key for provider '{provider_id}'.[/green]\n"
        "[dim]Restart nahida-bot to load the new credential.[/dim]"
    )


@auth_app.command(name="logout")
def logout(
    provider: str | None = typer.Argument(
        None,
        help="Configured provider id; prompts when omitted",
    ),
    config: str | None = typer.Option(
        None,
        "--config-yaml",
        "--config",
        "-c",
        help="Path to YAML configuration file",
    ),
) -> None:
    """Remove credentials stored by the auth command."""
    settings = load_settings_auto(config_yaml=config)
    provider_id = provider.strip() if provider else _select_provider(settings, None)
    if not provider_id:
        console.print("[red]Provider id must not be empty.[/red]")
        raise typer.Exit(1)
    api_key_removed, codex_removed = asyncio.run(
        _remove_credentials(provider_id, settings)
    )
    if not api_key_removed and not codex_removed:
        console.print(f"[yellow]No stored credentials for '{provider_id}'.[/yellow]")
        raise typer.Exit(1)
    console.print(f"[green]✓ Removed stored credentials for '{provider_id}'.[/green]")
    provider_config = settings.providers.get(provider_id)
    if provider_config is not None and provider_config.api_key:
        console.print(
            "[yellow]This provider still has an api_key in config or the "
            "environment; that fallback remains active.[/yellow]"
        )


def _print_credentials(config: str | None) -> None:
    settings = load_settings_auto(config_yaml=config)
    rows = asyncio.run(_credential_rows(settings))
    if not rows:
        console.print("[dim]No providers or stored credentials found.[/dim]")
        return
    table = Table(title="Provider Authentication")
    table.add_column("Provider", style="cyan")
    table.add_column("Type")
    table.add_column("Method")
    table.add_column("Status")
    for provider_id, provider_type, method, status in rows:
        table.add_row(provider_id, provider_type, method, status)
    console.print(table)


@auth_app.command(name="list")
def list_credentials(
    config: str | None = typer.Option(
        None,
        "--config-yaml",
        "--config",
        "-c",
        help="Path to YAML configuration file",
    ),
) -> None:
    """List configured providers and credential status without revealing secrets."""
    _print_credentials(config)


@auth_app.command(name="ls", hidden=True)
def list_credentials_alias(
    config: str | None = typer.Option(
        None,
        "--config-yaml",
        "--config",
        "-c",
        help="Path to YAML configuration file",
    ),
) -> None:
    """Alias for ``auth list``."""
    _print_credentials(config)
