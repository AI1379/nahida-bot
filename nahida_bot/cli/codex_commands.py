"""CLI commands for ChatGPT Codex (Plus/Pro) OAuth login.

Usage::

    nahida-bot codex login --provider codex
    nahida-bot codex status
    nahida-bot codex logout --provider codex

The login command runs the device-authorization flow: prints a URL and
code, waits for the user to approve in any browser, then persists the
refresh token to the ``codex_tokens`` table. The running bot picks up
the token on next request (no restart needed).
"""

from __future__ import annotations

import asyncio

import httpx
import structlog
import typer
from rich.console import Console

from nahida_bot.auth import (
    DEVICE_VERIFICATION_URL,
    poll_device_challenge,
    request_device_challenge,
    resolve_client_id,
    resolve_originator,
    to_codex_token,
    user_agent,
)
from nahida_bot.core.config import load_settings
from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.db.repositories.sqlite_codex_token_repo import (
    SQLiteCodexTokenRepository,
)

logger = structlog.get_logger(__name__)
console = Console()

codex_app = typer.Typer(
    help="ChatGPT Codex (Plus/Pro) OAuth login and token management"
)

_CONFIG_YAML_CTX = "config_yaml"


def _config_callback(ctx: typer.Context, config_yaml: str | None) -> None:
    if ctx.resilient_parsing:
        return
    ctx.ensure_object(dict)
    ctx.obj[_CONFIG_YAML_CTX] = config_yaml


@codex_app.callback()
def codex_root(
    ctx: typer.Context,
    config: str | None = typer.Option(
        None, "--config", "-c", help="Path to YAML configuration file"
    ),
) -> None:
    """ChatGPT Codex (Plus/Pro) OAuth login and token management."""
    _config_callback(ctx, config)


def _get_config_yaml(ctx: typer.Context) -> str | None:
    obj = ctx.obj or {}
    return obj.get(_CONFIG_YAML_CTX)


async def _run_login(provider_id: str, config_yaml: str | None) -> int:
    settings = load_settings(config_yaml=config_yaml)
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
        masked_refresh = codex_token.refresh_token[:6] + "…"
        console.print(
            f"\n[bold green]✓ Login successful[/bold green]\n"
            f"  account_id: [cyan]{codex_token.account_id or '(unknown)'}[/cyan]\n"
            f"  refresh_token: [dim]{masked_refresh}[/dim]\n"
            f"  expires_at: epoch {codex_token.expires_at}\n"
            f"\nProvider '[cyan]{provider_id}[/cyan]' is ready to use."
        )
        return 0
    finally:
        await engine.close()


async def _run_logout(provider_id: str, config_yaml: str | None) -> int:
    settings = load_settings(config_yaml=config_yaml)
    engine = DatabaseEngine(settings.db_path)
    await engine.initialize()
    try:
        repo = SQLiteCodexTokenRepository(engine)
        removed = await repo.delete(provider_id)
        if removed:
            console.print(
                f"[green]✓ Removed Codex token for provider "
                f"'[cyan]{provider_id}[/cyan]'.[/green]"
            )
            return 0
        console.print(
            f"[yellow]No Codex token stored for provider "
            f"'[cyan]{provider_id}[/cyan]'.[/yellow]"
        )
        return 1
    finally:
        await engine.close()


async def _run_status(config_yaml: str | None) -> int:
    settings = load_settings(config_yaml=config_yaml)
    engine = DatabaseEngine(settings.db_path)
    await engine.initialize()
    try:
        repo = SQLiteCodexTokenRepository(engine)
        provider_ids = await repo.list_provider_ids()
        if not provider_ids:
            console.print("[dim]No Codex tokens stored.[/dim]")
            return 0
        import time

        from rich.table import Table

        table = Table(title="Codex OAuth Tokens")
        table.add_column("Provider", style="cyan")
        table.add_column("Account ID")
        table.add_column("Access Token", justify="center")
        table.add_column("Refresh Token", justify="center")

        now = time.time()
        for pid in provider_ids:
            token = await repo.get(pid)
            if token is None:
                continue
            access_state = (
                "[green]valid[/green]"
                if token.access_token and token.expires_at > now
                else "[yellow]expired[/yellow]"
            )
            refresh_state = (
                "[green]present[/green]"
                if token.refresh_token
                else "[red]missing[/red]"
            )
            table.add_row(
                pid,
                token.account_id or "(unknown)",
                access_state,
                refresh_state,
            )
        console.print(table)
        return 0
    finally:
        await engine.close()


@codex_app.command(name="login")
def login(
    ctx: typer.Context,
    provider: str = typer.Option(
        "codex",
        "--provider",
        "-p",
        help="Provider id (must match a 'type: codex' entry in config.yaml)",
    ),
) -> None:
    """Authorize nahida-bot to use a ChatGPT Plus/Pro subscription."""
    rc = asyncio.run(_run_login(provider, _get_config_yaml(ctx)))
    if rc != 0:
        raise typer.Exit(code=rc)


@codex_app.command(name="logout")
def logout(
    ctx: typer.Context,
    provider: str = typer.Option(
        "codex", "--provider", "-p", help="Provider id to forget"
    ),
) -> None:
    """Forget a stored Codex refresh token."""
    rc = asyncio.run(_run_logout(provider, _get_config_yaml(ctx)))
    if rc != 0:
        raise typer.Exit(code=rc)


@codex_app.command(name="status")
def status(ctx: typer.Context) -> None:
    """Show stored Codex tokens and whether the access token is fresh."""
    console.print(f"[dim]User-Agent: {user_agent()}[/dim]")
    rc = asyncio.run(_run_status(_get_config_yaml(ctx)))
    if rc != 0:
        raise typer.Exit(code=rc)
