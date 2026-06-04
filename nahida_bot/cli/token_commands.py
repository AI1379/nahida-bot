"""CLI commands for token usage statistics.

These commands read directly from the SQLite database so they work
whether or not the bot is currently running.
"""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

from nahida_bot.agent.usage import UsageRecorder
from nahida_bot.core.config import load_settings
from nahida_bot.db.engine import DatabaseEngine

token_app = typer.Typer(help="Token usage statistics and management")
console = Console()

_CONFIG_YAML_CTX = "config_yaml"


def _config_callback(ctx: typer.Context, config_yaml: str | None) -> None:
    """Store the --config option in the Typer context for subcommands."""
    if ctx.resilient_parsing:
        return
    ctx.ensure_object(dict)
    ctx.obj[_CONFIG_YAML_CTX] = config_yaml


@token_app.callback()
def token_root(
    ctx: typer.Context,
    config: str | None = typer.Option(
        None, "--config", "-c", help="Path to YAML configuration file"
    ),
) -> None:
    """Token usage statistics and management."""
    _config_callback(ctx, config)


def _format_tokens(n: int) -> str:
    """Format a token count with thousands separators."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _format_cost(cost: float | None) -> str:
    """Format a cost value in USD."""
    if cost is None:
        return "—"
    return f"${cost:.4f}"


async def _load_recorder(config_yaml: str | None = None) -> UsageRecorder | None:
    """Create a UsageRecorder and load data from the configured database."""
    settings = load_settings(config_yaml=config_yaml)
    db_path = settings.db_path
    if not db_path:
        console.print("[red]No database path configured.[/red]")
        return None

    from pathlib import Path

    if not Path(db_path).exists():
        console.print(f"[yellow]Database not found at {db_path}[/yellow]")
        return None

    engine = DatabaseEngine(db_path)
    await engine.initialize()
    recorder = UsageRecorder()
    await recorder.load_from_db(engine)
    await engine.close()
    return recorder


def _get_config_yaml(ctx: typer.Context) -> str | None:
    """Extract the config YAML path from the Typer context."""
    obj = ctx.obj or {}
    return obj.get(_CONFIG_YAML_CTX)


@token_app.command(name="stats")
def stats(
    ctx: typer.Context,
    provider: str = typer.Option("", "--provider", "-p", help="Filter by provider ID"),
    days: int = typer.Option(7, "--days", "-d", help="Days for daily breakdown"),
) -> None:
    """Show aggregate token usage statistics."""
    recorder = asyncio.run(_load_recorder(_get_config_yaml(ctx)))
    if recorder is None:
        return

    totals = recorder.get_totals(
        provider_id=provider or None,
    )
    by_provider = recorder.get_by_provider()
    daily = recorder.get_daily_breakdown(days=days, provider_id=provider or None)

    # Totals table
    totals_table = Table(title="Token Usage Totals")
    totals_table.add_column("Metric", style="cyan")
    totals_table.add_column("Count", justify="right")

    totals_table.add_row("Input tokens", _format_tokens(totals.input_tokens))
    totals_table.add_row("Output tokens", _format_tokens(totals.output_tokens))
    totals_table.add_row("Cached tokens", _format_tokens(totals.cached_tokens))
    totals_table.add_row("Reasoning tokens", _format_tokens(totals.reasoning_tokens))
    totals_table.add_row("Cache creation", _format_tokens(totals.cache_creation_tokens))
    totals_table.add_row("Total events", str(totals.event_count))
    totals_table.add_row("Estimated cost", _format_cost(totals.estimated_cost))

    console.print(totals_table)
    console.print()

    # Per-provider table
    if by_provider:
        provider_table = Table(title="Usage by Provider")
        provider_table.add_column("Provider", style="cyan")
        provider_table.add_column("Model")
        provider_table.add_column("Input", justify="right")
        provider_table.add_column("Output", justify="right")
        provider_table.add_column("Cached", justify="right")
        provider_table.add_column("Events", justify="right")
        provider_table.add_column("Cost", justify="right")
        provider_table.add_column("Est.", justify="center")

        for s in by_provider:
            provider_table.add_row(
                s.provider_id,
                s.model,
                _format_tokens(s.input_tokens),
                _format_tokens(s.output_tokens),
                _format_tokens(s.cached_tokens),
                str(s.event_count),
                _format_cost(s.estimated_cost),
                "✓" if s.estimated else "",
            )

        console.print(provider_table)
        console.print()

    # Daily breakdown
    if daily:
        daily_table = Table(title=f"Daily Breakdown (last {days} days)")
        daily_table.add_column("Date", style="cyan")
        daily_table.add_column("Input", justify="right")
        daily_table.add_column("Output", justify="right")
        daily_table.add_column("Provider")

        for d in daily:
            daily_table.add_row(
                d.date,
                _format_tokens(d.input_tokens),
                _format_tokens(d.output_tokens),
                d.provider_id,
            )

        console.print(daily_table)


@token_app.command(name="list")
def list_events(
    ctx: typer.Context,
    limit: int = typer.Option(20, "--limit", "-n", help="Number of events to show"),
    provider: str = typer.Option("", "--provider", "-p", help="Filter by provider ID"),
) -> None:
    """Show recent token usage events."""
    config_yaml = _get_config_yaml(ctx)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    recorder = loop.run_until_complete(_load_recorder(config_yaml))
    if recorder is None:
        return

    events = loop.run_until_complete(
        recorder.get_events_from_db(limit=limit, provider_id=provider or None)
    )
    loop.close()

    if not events:
        console.print("[dim]No usage events recorded yet.[/dim]")
        return

    table = Table(title=f"Recent Token Usage Events (last {len(events)})")
    table.add_column("Time", style="cyan")
    table.add_column("Provider")
    table.add_column("Model")
    table.add_column("Input", justify="right")
    table.add_column("Output", justify="right")
    table.add_column("Cost", justify="right")

    for ev in events:
        ts_short = ev.timestamp[:19] if ev.timestamp else ""
        table.add_row(
            ts_short,
            ev.provider_id,
            ev.model,
            _format_tokens(ev.input_tokens),
            _format_tokens(ev.output_tokens),
            _format_cost(ev.estimated_cost),
        )

    console.print(table)


@token_app.command(name="providers")
def list_providers(ctx: typer.Context) -> None:
    """Show per-provider token consumption breakdown."""
    recorder = asyncio.run(_load_recorder(_get_config_yaml(ctx)))
    if recorder is None:
        return

    by_provider = recorder.get_by_provider()
    if not by_provider:
        console.print("[dim]No usage data recorded yet.[/dim]")
        return

    table = Table(title="Provider Token Consumption")
    table.add_column("Provider", style="cyan")
    table.add_column("Model")
    table.add_column("Input", justify="right")
    table.add_column("Output", justify="right")
    table.add_column("Total", justify="right")
    table.add_column("Events", justify="right")
    table.add_column("Cost", justify="right")

    for s in by_provider:
        total = s.input_tokens + s.output_tokens
        table.add_row(
            s.provider_id,
            s.model,
            _format_tokens(s.input_tokens),
            _format_tokens(s.output_tokens),
            _format_tokens(total),
            str(s.event_count),
            _format_cost(s.estimated_cost),
        )

    console.print(table)


@token_app.command(name="clear")
def clear(
    ctx: typer.Context,
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
) -> None:
    """Clear all token usage history."""
    if not force:
        typer.confirm(
            "This will delete all token usage records. Continue?",
            abort=True,
        )

    config_yaml = _get_config_yaml(ctx)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    recorder = loop.run_until_complete(_load_recorder(config_yaml))
    if recorder is not None:
        loop.run_until_complete(recorder.clear())
        console.print("[green]Token usage history cleared.[/green]")
    loop.close()
