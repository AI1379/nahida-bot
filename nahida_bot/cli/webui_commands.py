"""CLI utilities for WebUI administration."""

from __future__ import annotations

import typer
from rich.console import Console

from nahida_bot.gateway.services.webui_auth import hash_password_pbkdf2

console = Console()
webui_app = typer.Typer(help="WebUI administration utilities")


@webui_app.command(name="hash-password")
def hash_password() -> None:
    """Interactively generate a WebUI PBKDF2 password hash."""
    password = typer.prompt(
        "WebUI password",
        hide_input=True,
        confirmation_prompt=True,
    )
    if not password:
        console.print("[red]Password must not be empty.[/red]")
        raise typer.Exit(1)
    digest = hash_password_pbkdf2(password)
    console.print("[green]Generated WebUI password hash:[/green]")
    console.print(digest, markup=False)
    console.print("\n[dim]Set it as webui.auth.admin_password_hash.[/dim]")
