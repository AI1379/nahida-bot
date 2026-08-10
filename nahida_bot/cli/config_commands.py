"""Configuration management subcommands: schema and validate.

CLI presentation layer only — business logic lives in
nahida_bot.core.config_schema and nahida_bot.core.config_validation.
"""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from nahida_bot.core.config import load_settings_auto
from nahida_bot.core.config_schema import build_config_schema
from nahida_bot.core.config_validation import validate_settings

config_app = typer.Typer(help="Configuration management")
console = Console()


# ---------------------------------------------------------------------------
# config schema
# ---------------------------------------------------------------------------


@config_app.command(name="schema")
def schema_cmd(
    section: str | None = typer.Option(
        None,
        "--section",
        "-s",
        help="Filter to a config section (e.g. memory, memory.embedding, scheduler)",
    ),
    output_format: str = typer.Option(
        "table", "--format", "-f", help="Output format: table, json"
    ),
    show_providers: bool = typer.Option(
        False, "--providers", help="Also show ProviderEntryConfig fields"
    ),
    show_plugins: bool = typer.Option(
        True,
        "--plugins/--no-plugins",
        help="Include discovered plugin configuration entries",
    ),
    config_yaml: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to YAML configuration file; used for plugin_paths discovery",
    ),
) -> None:
    """Print all configuration keys with types and defaults.

    Use --section to narrow down (e.g. -s memory.embedding).
    Use --providers to also expand the per-provider model schema.
    """
    entries = build_config_schema(
        section,
        show_providers,
        show_plugins=show_plugins,
        config_yaml=config_yaml,
    )

    if output_format == "json":
        console.out(
            json.dumps(
                [
                    {
                        "path": e.path,
                        "type": e.type_,
                        "default": e.default_,
                        "constraints": e.constraints,
                    }
                    for e in entries
                ],
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    table = Table(title="Configuration Schema", highlight=True)
    table.add_column("Path", style="cyan", no_wrap=True)
    table.add_column("Type", style="yellow")
    table.add_column("Default", style="green")
    for e in entries:
        constraints = f" [{e.constraints}]" if e.constraints != "-" else ""
        table.add_row(Text(e.path), Text(e.type_), Text(e.default_ + constraints))
    console.print(table)


# ---------------------------------------------------------------------------
# config validate
# ---------------------------------------------------------------------------


@config_app.command(name="validate")
def validate_cmd(
    config_yaml: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to YAML configuration file",
    ),
) -> None:
    """Validate configuration for common issues and inconsistencies.

    Checks include:
    - default_provider refers to a defined provider
    - internal model spec references can potentially resolve
    - provider entries have api_key set
    - sqlite-vec dependency and dimension setup
    - multimodal fallback model is set when fallback mode is enabled
    """
    try:
        settings = load_settings_auto(config_yaml=config_yaml)
    except Exception as exc:
        from pydantic import ValidationError

        if isinstance(exc, ValidationError):
            console.print("[bold red]Configuration validation failed:[/bold red]\n")
            for error in exc.errors():
                loc = ".".join(str(p) for p in error["loc"])
                console.print(f"  [cyan]{loc}[/cyan]  {error['msg']}")
            console.print(
                "\n[yellow]Hint:[/yellow] Check that all "
                "${VAR} placeholders resolve to actual values."
            )
        else:
            console.print(f"[bold red]Failed to load config:[/bold red] {exc}")
        raise typer.Exit(1)

    report = validate_settings(settings)
    _print_report(report)

    if not report.ok:
        raise typer.Exit(1)


def _print_report(report) -> None:
    if not report.issues:
        console.print(
            "[bold green]OK - Configuration is valid, no issues found.[/bold green]"
        )
        return

    for issue in report.issues:
        if issue.severity == "error":
            prefix = "[bold red]ERROR[/bold red]"
        else:
            prefix = "[bold yellow]WARN [/bold yellow]"
        console.print(f"  {prefix}  [cyan]{issue.path}[/cyan]  {issue.message}")

    summary_color = "red" if report.errors else "yellow"
    console.print(
        f"\n[bold {summary_color}]{report.errors} error(s), {report.warnings} warning(s)[/bold {summary_color}]"
    )
