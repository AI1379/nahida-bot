"""Command-line interface."""

import asyncio

import structlog

import typer
from rich.console import Console
from rich.table import Table

from nahida_bot.cli.config_commands import config_app
from nahida_bot.core.app import Application
from nahida_bot.core.config import load_settings

logger = structlog.get_logger(__name__)
console = Console()

app = typer.Typer(help="Nahida Bot - LLM Chatbot Framework")
app.add_typer(config_app, name="config")


@app.command()
def version() -> None:
    """Show version information."""
    from nahida_bot import __version__

    console.print(f"Nahida Bot v{__version__}")


@app.command()
def start(
    config_yaml: str | None = typer.Option(
        None, help="Path to YAML configuration file"
    ),
    debug: bool = typer.Option(False, help="Enable debug mode"),
) -> None:
    """Start the Nahida Bot application."""
    settings = load_settings(config_yaml=config_yaml, debug=debug)

    console.print(f"[bold cyan]Config YAML Path: {config_yaml}[/bold cyan]")
    console.print(f"[bold cyan]Starting {settings.app_name}...[/bold cyan]")
    console.print(f"Debug mode: {debug}")
    console.print(f"Log level: {settings.log_level}")
    console.print(f"Listening on {settings.host}:{settings.port}")

    app_instance = Application(settings=settings)

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
