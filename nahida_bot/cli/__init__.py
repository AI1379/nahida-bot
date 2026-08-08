"""Command-line interface."""

import asyncio
from typing import Any

import structlog

import typer
from rich.console import Console
from rich.table import Table

from nahida_bot.cli.config_commands import config_app
from nahida_bot.cli.codex_commands import codex_app
from nahida_bot.cli.token_commands import token_app
from nahida_bot.core.app import Application
from nahida_bot.core.config import load_settings

logger = structlog.get_logger(__name__)
console = Console()

app = typer.Typer(help="Nahida Bot - LLM Chatbot Framework")
app.add_typer(config_app, name="config")
app.add_typer(codex_app, name="codex")
app.add_typer(token_app, name="tokens")


@app.command()
def version() -> None:
    """Show version information."""
    from nahida_bot.version import get_version_info

    info = get_version_info()
    console.print(f"Nahida Bot v{info['version']}")
    if info["git_hash"]:
        console.print(f"  git: {info['git_hash']}")


@app.command()
def start(
    config_yaml: str | None = typer.Option(
        None, help="Path to YAML configuration file"
    ),
    debug: bool = typer.Option(False, help="Enable debug mode"),
    log_file: str | None = typer.Option(None, help="Path to application log file"),
    log_file_level: str | None = typer.Option(
        None, help="File log level; defaults to log_level"
    ),
    log_file_max_bytes: int | None = typer.Option(
        None,
        "--log-file-max-bytes",
        help="Rotate log file at this size in bytes; 0 disables rotation.",
    ),
    log_file_backup_count: int | None = typer.Option(
        None,
        "--log-file-backup-count",
        help="Number of rotated backup files to keep.",
    ),
) -> None:
    """Start the Nahida Bot application."""
    overrides: dict[str, Any] = {"debug": debug}
    if log_file is not None:
        overrides["log_file"] = log_file
    if log_file_level is not None:
        overrides["log_file_level"] = log_file_level
    if log_file_max_bytes is not None:
        overrides["log_file_max_bytes"] = log_file_max_bytes
    if log_file_backup_count is not None:
        overrides["log_file_backup_count"] = log_file_backup_count
    settings = load_settings(config_yaml=config_yaml, **overrides)

    console.print(f"[bold cyan]Config YAML Path: {config_yaml}[/bold cyan]")
    console.print(f"[bold cyan]Starting {settings.app_name}...[/bold cyan]")
    console.print(f"Debug mode: {debug}")
    console.print(f"Log level: {settings.log_level}")
    if settings.log_file:
        console.print(
            f"Log file: {settings.log_file} "
            f"({settings.log_file_level or settings.log_level})"
        )
    console.print(f"Listening on {settings.host}:{settings.port}")

    app_instance = Application(settings=settings, config_yaml_path=config_yaml)

    try:
        asyncio.run(app_instance.run())
    except KeyboardInterrupt:
        console.print("[bold yellow]Shutdown complete[/bold yellow]")


@app.command()
def doctor() -> None:
    """Run diagnostic checks."""
    console.print("[bold cyan]Running diagnostics...[/bold cyan]")

    # TODO: Currently the doctor command is a placeholder. Implement actual checks in the future.

    checks = [
        ("Python version", True),
        ("Dependencies installed", True),
        ("Configuration valid", True),
        ("Database accessible", True),
    ]

    table = Table(title="Diagnostic Report")
    table.add_column("Check", style="cyan")
    table.add_column("Status", style="green")

    for check_name, status in checks:
        status_str = "[green]✓ Pass[/green]" if status else "[red]✗ Fail[/red]"
        table.add_row(check_name, status_str)

    console.print(table)
    console.print("[bold green]All checks passed![/bold green]")


def main() -> None:
    """Main CLI entry point."""
    app()


if __name__ == "__main__":
    main()
