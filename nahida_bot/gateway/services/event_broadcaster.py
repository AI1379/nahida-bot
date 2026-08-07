"""SSE event broadcaster: fans out in-process events to connected HTTP clients."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from nahida_bot.core.events import (
    AgentRunCancelled,
    AgentRunFinished,
    AgentRunStarted,
    AgentStopRequested,
    Event,
    MessageReceived,
    MessageSent,
    PluginErrorOccurred,
    ProcessFailed,
    ProcessStarted,
    ProcessStopped,
)
from nahida_bot.core.logging import get_log_capture
from nahida_bot.gateway.services.log_redaction import to_log_entry
from nahida_bot.gateway.services.status_service import collect_status

if TYPE_CHECKING:
    from nahida_bot.core.app import Application

logger = structlog.get_logger(__name__)

_CLIENT_QUEUE_MAXSIZE = 512
_LOG_BRIDGE_IGNORED_LOGGERS = ("sse_starlette",)


def _should_bridge_log_record(record: logging.LogRecord) -> bool:
    """Return whether a stdlib log record should be streamed to WebUI clients."""
    return not record.name.startswith(_LOG_BRIDGE_IGNORED_LOGGERS)


class _LogBridgeHandler(logging.Handler):
    """Pushes formatted log entries into the broadcaster's fan-out queues."""

    def __init__(self, broadcaster: EventBroadcaster) -> None:
        super().__init__()
        self._broadcaster = broadcaster

    def emit(self, record: logging.LogRecord) -> None:
        if not _should_bridge_log_record(record):
            return
        try:
            raw = self.format(record)
            entry = json.loads(raw)
            entry["logger"] = record.name
            self._broadcaster._push_event(
                "log.entry",
                to_log_entry(entry).model_dump(mode="json"),
            )
        except Exception:
            pass


class EventBroadcaster:
    """Collects events from multiple sources and fans them out to SSE clients."""

    def __init__(self, app: Application) -> None:
        self._app = app
        self._clients: set[asyncio.Queue[str | None]] = set()
        self._subscriptions: list[Any] = []
        self._log_handler: _LogBridgeHandler | None = None
        self._status_task: asyncio.Task[None] | None = None
        self._ping_task: asyncio.Task[None] | None = None
        self._scheduler_callback_set = False

    # -- Lifecycle --

    async def start(self) -> None:
        """Subscribe to event sources and start background tasks."""
        bus = self._app.event_bus

        self._subscriptions.append(
            bus.subscribe(
                MessageReceived,
                self._on_message_event,
                priority=10,
            )
        )
        self._subscriptions.append(
            bus.subscribe(
                MessageSent,
                self._on_message_event,
                priority=10,
            )
        )
        self._subscriptions.append(
            bus.subscribe(
                PluginErrorOccurred,
                self._on_plugin_error,
                priority=10,
            )
        )
        # Agent run lifecycle + stop requests → reactive webui run status
        # (replaces polling get_session_run_status).
        for run_event_type in (
            AgentRunStarted,
            AgentRunCancelled,
            AgentRunFinished,
            AgentStopRequested,
        ):
            self._subscriptions.append(
                bus.subscribe(run_event_type, self._on_agent_run_event, priority=10)
            )

        # Supervised process lifecycle (SSH tunnels, frpc, sidecars).
        for process_event_type in (ProcessStarted, ProcessStopped, ProcessFailed):
            self._subscriptions.append(
                bus.subscribe(process_event_type, self._on_process_event, priority=10)
            )

        # Bridge log capture
        capture = get_log_capture()
        if capture is not None:
            self._log_handler = _LogBridgeHandler(self)
            # Reuse the same formatter as the capture handler
            if capture.formatter:
                self._log_handler.setFormatter(capture.formatter)
            logging.getLogger().addHandler(self._log_handler)

        # Periodic status sampler
        self._status_task = asyncio.create_task(self._status_sampler())

        # Keep-alive ping
        self._ping_task = asyncio.create_task(self._ping_loop())

        # Scheduler callback
        self._setup_scheduler_callback()

        logger.info("event_broadcaster.started")

    async def stop(self) -> None:
        """Unsubscribe and cancel background tasks."""
        for sub in self._subscriptions:
            sub.unsubscribe()
        self._subscriptions.clear()

        if self._log_handler is not None:
            logging.getLogger().removeHandler(self._log_handler)
            self._log_handler = None

        if self._status_task is not None:
            self._status_task.cancel()
            self._status_task = None

        if self._ping_task is not None:
            self._ping_task.cancel()
            self._ping_task = None

        # Drain remaining events to clients
        for q in list(self._clients):
            self._put_with_drop_oldest(q, None)  # signal disconnect
        self._clients.clear()

        logger.info("event_broadcaster.stopped")

    # -- Client management --

    def subscribe(self) -> asyncio.Queue[str | None]:
        """Create a new client queue and return it."""
        q: asyncio.Queue[str | None] = asyncio.Queue(maxsize=_CLIENT_QUEUE_MAXSIZE)
        self._clients.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str | None]) -> None:
        """Remove a client queue."""
        self._clients.discard(q)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    # -- Internal: push events --

    def _push_event(self, event_type: str, data: Any) -> None:
        """Fan out an SSE event to all connected clients."""
        payload = f"event: {event_type}\ndata: {json.dumps(data, default=str)}\n\n"
        dead: list[asyncio.Queue[str | None]] = []
        for q in list(self._clients):
            if not self._put_with_drop_oldest(q, payload):
                dead.append(q)
        for q in dead:
            self._clients.discard(q)

    @staticmethod
    def _put_with_drop_oldest(q: asyncio.Queue[str | None], item: str | None) -> bool:
        """Put into a bounded client queue, dropping stale events first."""
        try:
            q.put_nowait(item)
            return True
        except asyncio.QueueFull:
            pass

        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            pass

        try:
            q.put_nowait(item)
            return True
        except asyncio.QueueFull:
            return False

    # -- EventBus handlers --

    async def _on_message_event(self, event: Event[Any], ctx: Any) -> None:
        from nahida_bot.core.events import MessageReceived

        event_name = (
            "message.received" if isinstance(event, MessageReceived) else "message.sent"
        )
        payload = event.payload
        self._push_event(event_name, {"session_id": payload.session_id})

    async def _on_plugin_error(self, event: Event[Any], ctx: Any) -> None:
        payload = event.payload
        self._push_event(
            "plugin.error",
            {
                "plugin_id": payload.plugin_id,
                "method": payload.method,
                "error": payload.error,
            },
        )

    async def _on_agent_run_event(self, event: Event[Any], ctx: Any) -> None:
        payload = event.payload
        if isinstance(event, AgentStopRequested):
            event_name = "agent_run.stop_requested"
            workspace_id = ""
            terminal = ""
            error = ""
        elif isinstance(event, AgentRunStarted):
            event_name = "agent_run.started"
            workspace_id = payload.workspace_id
            terminal = payload.terminal
            error = payload.error
        elif isinstance(event, AgentRunCancelled):
            event_name = "agent_run.cancelled"
            workspace_id = payload.workspace_id
            terminal = payload.terminal
            error = payload.error
        else:
            event_name = "agent_run.finished"
            workspace_id = payload.workspace_id
            terminal = payload.terminal
            error = payload.error
        self._push_event(
            event_name,
            {
                "session_id": payload.session_id,
                "workspace_id": workspace_id,
                "terminal": terminal,
                "error": error,
            },
        )

    async def _on_process_event(self, event: Event[Any], ctx: Any) -> None:
        if isinstance(event, ProcessStarted):
            event_name = "process.started"
        elif isinstance(event, ProcessStopped):
            event_name = "process.stopped"
        else:
            event_name = "process.failed"
        payload = event.payload
        self._push_event(
            event_name,
            {
                "name": payload.name,
                "owner": payload.owner,
                "status": payload.status,
                "pid": payload.pid,
                "restart_count": payload.restart_count,
                "exit_code": payload.exit_code,
                "error": payload.error,
            },
        )

    # -- Scheduler callback --

    def _setup_scheduler_callback(self) -> None:
        scheduler = self._app.scheduler_service
        if scheduler is None or self._scheduler_callback_set:
            return
        scheduler.on_job_event = self._on_scheduler_event
        self._scheduler_callback_set = True

    async def _on_scheduler_event(
        self, event_type: str, job_id: str, **kwargs: Any
    ) -> None:
        data: dict[str, Any] = {"job_id": job_id}
        data.update(kwargs)
        self._push_event(f"cron.{event_type}", data)

    def notify_cron_updated(self, job_id: str, action: str) -> None:
        self._push_event("cron.updated", {"job_id": job_id, "action": action})

    # -- Public: push config events from route handlers --

    def notify_config_saved(
        self, backup_path: str | None, restart_required: bool
    ) -> None:
        self._push_event(
            "config.saved",
            {
                "backup_path": backup_path,
                "restart_required": restart_required,
            },
        )

    # -- Background tasks --

    async def _status_sampler(self) -> None:
        """Periodically sample system status and push to clients."""
        try:
            while True:
                await asyncio.sleep(5)
                if not self._clients:
                    continue
                try:
                    status = collect_status(self._app)
                    usage = (
                        self._app._usage_ledger.get_totals()
                        if self._app._usage_ledger
                        else None
                    )
                    self._push_event(
                        "status.updated",
                        {
                            "app": {
                                "name": status.name,
                                "version": status.version,
                                "debug": status.debug,
                                "started": status.started,
                                "started_at": status.started_at,
                                "uptime_seconds": status.uptime_seconds,
                                "pid": status.pid,
                            },
                            "resources": {
                                "cpu_percent": status.resources.cpu_percent,
                                "memory_rss_bytes": status.resources.memory_rss_bytes,
                                "memory_percent": status.resources.memory_percent,
                                "disk_free_bytes": status.resources.disk_free_bytes,
                                "db_size_bytes": status.resources.db_size_bytes,
                                "workspace_size_bytes": status.resources.workspace_size_bytes,
                            },
                            "services": {
                                "router": status.services.router,
                                "scheduler": status.services.scheduler,
                                "webapi": status.services.webapi,
                                "memory": status.services.memory,
                                "workspace": status.services.workspace,
                            },
                            "usage": {
                                "input_tokens": usage.input_tokens if usage else 0,
                                "output_tokens": usage.output_tokens if usage else 0,
                                "cached_tokens": usage.cached_tokens if usage else 0,
                                "reasoning_tokens": usage.reasoning_tokens
                                if usage
                                else 0,
                                "estimated_cost": usage.estimated_cost
                                if usage
                                else None,
                                "currency": None,
                            },
                        },
                    )
                except Exception:
                    logger.debug("event_broadcaster.status_sample_failed")
        except asyncio.CancelledError:
            pass

    async def _ping_loop(self) -> None:
        """Send periodic keep-alive pings."""
        try:
            while True:
                await asyncio.sleep(15)
                self._push_event("ping", {"ts": datetime.now(UTC).isoformat()})
        except asyncio.CancelledError:
            pass
