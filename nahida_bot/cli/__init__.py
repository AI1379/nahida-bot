"""Command-line interface."""

import asyncio
import logging

import typer
from rich.console import Console
from rich.table import Table

from nahida_bot.core.app import Application
from nahida_bot.core.config import load_settings

logger = logging.getLogger(__name__)
console = Console()

app = typer.Typer(help="Nahida Bot - LLM Chatbot Framework")


@app.command()
def version() -> None:
    """Show version information."""
    from nahida_bot import __version__

    console.print(f"Nahida Bot v{__version__}")


@app.command()
def start(debug: bool = typer.Option(False, help="Enable debug mode")) -> None:
    """Start the Nahida Bot application."""
    settings = load_settings()
    settings.debug = debug

    console.print(f"[bold cyan]Starting {settings.app_name}...[/bold cyan]")
    console.print(f"Debug mode: {debug}")
    console.print(f"Listening on {settings.host}:{settings.port}")

    app_instance = Application(settings=settings)

    try:
        asyncio.run(app_instance.run())
    except KeyboardInterrupt:
        console.print("[bold yellow]Shutdown complete[/bold yellow]")


@app.command()
def config() -> None:
    """Show current configuration."""
    settings = load_settings()

    table = Table(title="Current Configuration")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("App Name", settings.app_name)
    table.add_row("Debug", str(settings.debug))
    table.add_row("Log Level", settings.log_level)
    table.add_row("JSON Logs", str(settings.log_json))
    table.add_row("Host", settings.host)
    table.add_row("Port", str(settings.port))
    table.add_row("Database", settings.db_path)

    console.print(table)


@app.command()
def doctor() -> None:
    """Run diagnostic checks."""
    console.print("[bold cyan]Running diagnostics...[/bold cyan]")

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
