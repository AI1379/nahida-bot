"""Command-line interface."""

import asyncio
from typing import Any

import structlog

import typer
from rich.console import Console
from rich.table import Table

from nahida_bot.cli.auth_commands import auth_app
from nahida_bot.cli.bootstrap_commands import bootstrap_app
from nahida_bot.cli.config_commands import config_app
from nahida_bot.cli.token_commands import token_app
from nahida_bot.cli.webui_commands import webui_app
from nahida_bot.core.app import Application
from nahida_bot.core.config import (
    find_config_yaml,
    find_env_path,
    load_settings,
)
from nahida_bot.core.preflight import check_readiness

logger = structlog.get_logger(__name__)
console = Console()

app = typer.Typer(help="Nahida Bot - LLM Chatbot Framework")
app.add_typer(bootstrap_app, name="bootstrap")
app.add_typer(config_app, name="config")
app.add_typer(auth_app, name="auth")
app.add_typer(webui_app, name="webui")
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
        None,
        "--config-yaml",
        "--config",
        "-c",
        help="Path to YAML configuration file (default: ./config.yaml or $NAHIDA_CONFIG)",
    ),
    env_path: str | None = typer.Option(
        None,
        "--env",
        help="Path to .env file (default: ./.env or $ENV_PATH)",
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
    skip_preflight: bool = typer.Option(
        False,
        "--skip-preflight",
        help="Skip the pre-flight readiness checks before starting.",
    ),
) -> None:
    """Start the Nahida Bot application."""
    resolved_yaml = find_config_yaml(config_yaml)
    resolved_env = find_env_path(env_path)

    overrides: dict[str, Any] = {"debug": debug}
    if log_file is not None:
        overrides["log_file"] = log_file
    if log_file_level is not None:
        overrides["log_file_level"] = log_file_level
    if log_file_max_bytes is not None:
        overrides["log_file_max_bytes"] = log_file_max_bytes
    if log_file_backup_count is not None:
        overrides["log_file_backup_count"] = log_file_backup_count
    settings = load_settings(
        config_yaml=resolved_yaml, env_path=resolved_env, **overrides
    )

    console.print(
        f"[bold cyan]Config:[/bold cyan] {resolved_yaml or '(defaults, no config.yaml found)'}"
    )
    console.print(
        f"[bold cyan]Env:[/bold cyan]     {resolved_env or '(no .env found)'}"
    )
    console.print(f"[bold cyan]Starting {settings.app_name}...[/bold cyan]")
    console.print(f"Debug mode: {debug}")
    console.print(f"Log level: {settings.log_level}")
    if settings.log_file:
        console.print(
            f"Log file: {settings.log_file} "
            f"({settings.log_file_level or settings.log_level})"
        )
    console.print(f"Listening on {settings.host}:{settings.port}")

    # Pre-flight readiness checks: warn loudly when the bot would come up but
    # be unable to serve (no usable provider, unresolved tokens, ...).
    if not skip_preflight:
        from nahida_bot.db.repositories.sqlite_provider_credential_repo import (
            stored_provider_ids,
        )

        report = check_readiness(
            settings,
            authenticated_provider_ids=stored_provider_ids(settings.db_path),
        )
        if report.issues:
            console.print("[bold yellow]Pre-flight checks found issues:[/bold yellow]")
            for issue in report.issues:
                if issue.severity == "error":
                    tag = "[bold red]ERROR[/bold red]"
                else:
                    tag = "[bold yellow]WARN [/bold yellow]"
                console.print(f"  {tag} {issue.message}")
                if issue.hint:
                    console.print(f"         [dim]{issue.hint}[/dim]")
            if report.errors:
                console.print(
                    "\n[bold red]Blocking errors detected. Aborting start.[/bold red]"
                )
                raise typer.Exit(1)
            console.print(
                "\n[dim]Continuing because these are warnings. The bot may be "
                "unable to respond until fixed.[/dim]"
            )

    app_instance = Application(settings=settings, config_yaml_path=resolved_yaml)

    try:
        asyncio.run(app_instance.run())
    except KeyboardInterrupt:
        console.print("[bold yellow]Shutdown complete[/bold yellow]")


@app.command()
def doctor(
    config_yaml: str | None = typer.Option(
        None,
        "--config-yaml",
        "--config",
        "-c",
        help="Path to YAML configuration file (default: ./config.yaml)",
    ),
    env_path: str | None = typer.Option(
        None,
        "--env",
        help="Path to .env file (default: ./.env)",
    ),
) -> None:
    """Run diagnostic checks against the current environment and config."""
    import shutil
    import sys
    from pathlib import Path

    from nahida_bot.core.config_validation import validate_settings
    from nahida_bot.core.preflight import check_readiness

    console.print("[bold cyan]Running diagnostics...[/bold cyan]\n")

    # Each row: (name, severity, detail) where severity in {pass, warn, fail}.
    rows: list[tuple[str, str, str]] = []

    py_ok = sys.version_info >= (3, 12)
    rows.append(
        (
            "Python >= 3.12",
            "pass" if py_ok else "fail",
            f"current: {sys.version.split()[0]}",
        )
    )

    rows.append(
        (
            "uv installed",
            "pass" if shutil.which("uv") else "warn",
            "recommended for dependency management",
        )
    )

    resolved_yaml = find_config_yaml(config_yaml)
    rows.append(
        (
            "config.yaml found",
            "pass" if resolved_yaml else "warn",
            f"resolved: {resolved_yaml or '(none — using built-in defaults)'}",
        )
    )

    resolved_env = find_env_path(env_path)
    rows.append(
        (
            ".env found",
            "pass" if resolved_env else "warn",
            f"resolved: {resolved_env or '(none)'}",
        )
    )

    settings = None
    try:
        settings = load_settings(config_yaml=resolved_yaml, env_path=resolved_env)
        rows.append(("config loads & parses", "pass", "settings loaded"))
    except Exception as exc:
        rows.append(("config loads & parses", "fail", f"{type(exc).__name__}: {exc}"))

    if settings is not None:
        from nahida_bot.db.repositories.sqlite_provider_credential_repo import (
            stored_provider_ids,
        )

        authenticated_provider_ids = stored_provider_ids(settings.db_path)
        vreport = validate_settings(
            settings,
            authenticated_provider_ids=authenticated_provider_ids,
        )
        sev = "pass" if not vreport.issues else ("fail" if vreport.errors else "warn")
        rows.append(
            (
                "config validate",
                sev,
                f"{vreport.errors} error(s), {vreport.warnings} warning(s)"
                if vreport.issues
                else "no issues",
            )
        )

        pre = check_readiness(
            settings,
            authenticated_provider_ids=authenticated_provider_ids,
        )
        sev = "pass" if not pre.issues else ("fail" if pre.errors else "warn")
        rows.append(
            (
                "readiness (provider)",
                sev,
                "; ".join(i.message for i in pre.issues)
                or "at least one usable provider configured",
            )
        )

        db_path = Path(settings.db_path)
        try:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            probe = db_path.parent / ".nahida_doctor_probe"
            probe.touch()
            probe.unlink()
            rows.append(("db path writable", "pass", str(db_path)))
        except OSError as exc:
            rows.append(("db path writable", "fail", f"not writable: {exc}"))
    else:
        rows.append(("config validate", "fail", "skipped (settings failed to load)"))
        rows.append(("readiness (provider)", "fail", "skipped"))
        rows.append(("db path writable", "fail", "skipped"))

    webui_dist = Path("webui/dist/index.html")
    rows.append(
        (
            "WebUI built",
            "pass" if webui_dist.is_file() else "warn",
            "webui/dist present"
            if webui_dist.is_file()
            else "run `pnpm webui:build` to enable the WebUI",
        )
    )

    table = Table(title="Diagnostic Report")
    table.add_column("Check", style="cyan")
    table.add_column("Status")
    table.add_column("Detail", style="dim")
    for name, sev, detail in rows:
        if sev == "pass":
            status_str = "[green]PASS[/green]"
        elif sev == "warn":
            status_str = "[yellow]WARN[/yellow]"
        else:
            status_str = "[red]FAIL[/red]"
        table.add_row(name, status_str, detail)
    console.print(table)

    if any(sev == "fail" for _, sev, _ in rows):
        console.print("[bold red]Some checks failed. See the table above.[/bold red]")
        raise typer.Exit(1)
    console.print("[bold green]No blocking issues found.[/bold green]")


def main() -> None:
    """Main CLI entry point."""
    app()


if __name__ == "__main__":
    main()
