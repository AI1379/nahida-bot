"""System status aggregation service."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from nahida_bot.core.app import Application

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class ResourceInfo:
    cpu_percent: float = 0.0
    memory_rss_bytes: int = 0
    memory_percent: float = 0.0
    disk_free_bytes: int = 0
    db_size_bytes: int = 0
    workspace_size_bytes: int = 0


@dataclass(slots=True)
class ServiceStatus:
    router: str = "unknown"
    scheduler: str = "unknown"
    webapi: str = "unknown"
    memory: str = "unknown"
    workspace: str = "unknown"
    providers: list[str] = field(default_factory=list)
    channels: list[str] = field(default_factory=list)
    plugins: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class UsageTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    estimated_cost: float | None = None
    currency: str | None = None


@dataclass(slots=True)
class AppStatus:
    name: str = ""
    version: str = ""
    debug: bool = False
    started: bool = False
    started_at: str | None = None
    uptime_seconds: float = 0.0
    pid: int = 0

    resources: ResourceInfo = field(default_factory=ResourceInfo)
    services: ServiceStatus = field(default_factory=ServiceStatus)
    usage: UsageTotals = field(default_factory=UsageTotals)
    capabilities: dict[str, bool] = field(default_factory=dict)


def collect_status(app: Application) -> AppStatus:
    """Build a full AppStatus snapshot from the running Application."""
    status = AppStatus(
        name=app.settings.app_name,
        version=app.version,
        debug=app.settings.debug,
        started=app.is_started,
        started_at=app.started_at.isoformat() if app.started_at else None,
        uptime_seconds=(
            (datetime.now(UTC) - app.started_at).total_seconds()
            if app.started_at
            else 0.0
        ),
        pid=os.getpid(),
    )

    # Resources
    status.resources = _collect_resources(app)
    # Services
    status.services = _collect_services(app)
    # Capabilities
    status.capabilities = {"resources": status.resources.cpu_percent > 0 or True}

    return status


def _collect_resources(app: Application) -> ResourceInfo:
    info = ResourceInfo()
    try:
        import psutil

        proc = psutil.Process(os.getpid())
        info.cpu_percent = proc.cpu_percent(interval=0) or 0.0
        mem_info = proc.memory_info()
        info.memory_rss_bytes = mem_info.rss
        info.memory_percent = proc.memory_percent()
    except ImportError:
        logger.debug("status.psutil_unavailable")

    try:
        db_path = Path(app.settings.db_path)
        if db_path.exists():
            info.db_size_bytes = db_path.stat().st_size
    except Exception:
        pass

    try:
        ws_base = Path(app.settings.workspace_base_dir)
        if ws_base.exists():
            total = sum(f.stat().st_size for f in ws_base.rglob("*") if f.is_file())
            info.workspace_size_bytes = total
    except Exception:
        pass

    try:
        info.disk_free_bytes = os.statvfs(".").f_bavail * os.statvfs(".").f_frsize  # type: ignore[attr-defined]
    except AttributeError:
        # Windows — use psutil if available, else skip
        try:
            import psutil

            info.disk_free_bytes = psutil.disk_usage(".").free
        except Exception:
            pass

    return info


def _collect_services(app: Application) -> ServiceStatus:
    svc = ServiceStatus()

    svc.router = "running" if app.message_router is not None else "stopped"
    svc.scheduler = "running" if app.scheduler_service is not None else "stopped"
    svc.webapi = "running" if app.webapi_service is not None else "stopped"
    svc.memory = "running" if app.memory_store is not None else "stopped"
    svc.workspace = "running" if app.workspace_manager is not None else "stopped"

    if app._provider_manager is not None:
        svc.providers = app._provider_manager.slot_ids

    if app.channel_registry is not None:
        # ChannelRegistry has no public list method; access the private dict
        # but only read keys.
        svc.channels = list(app.channel_registry._channels.keys())

    if app.plugin_manager is not None:
        from nahida_bot.plugins.manager import PluginState

        for record in app.plugin_manager.list_plugins():
            if record.state == PluginState.ERROR:
                svc.plugins[record.manifest.id] = "error"
            elif record.state == PluginState.ENABLED:
                svc.plugins[record.manifest.id] = "enabled"
            elif record.state == PluginState.LOADED:
                svc.plugins[record.manifest.id] = "loaded"
            elif record.state == PluginState.DISABLED:
                svc.plugins[record.manifest.id] = "disabled"
            else:
                svc.plugins[record.manifest.id] = "discovered"

    return svc
