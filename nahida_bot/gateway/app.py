"""WebAPI application: FastAPI + uvicorn lifecycle."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import time

import structlog
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from nahida_bot.gateway.auth import require_token
from nahida_bot.gateway.errors import register_error_handlers
from nahida_bot.gateway.services.webui_auth import WebUIAuthService

if TYPE_CHECKING:
    from nahida_bot.core.app import Application
    from nahida_bot.gateway.services.node_event_bridge import NodeEventBridge

logger = structlog.get_logger(__name__)

# Search webui/dist relative to the project root (cwd), not inside the package.
_WEBUI_SEARCH_PATHS = [
    Path.cwd() / "webui" / "dist",
    Path(__file__).resolve().parents[2] / "webui" / "dist",
]


class _RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every HTTP request with method, path, status, and duration."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        elapsed_ms = round((time.monotonic() - start) * 1000)

        logger.info(
            "webapi.request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            elapsed_ms=elapsed_ms,
        )
        return response


class WebAPIApp:
    """Manages the FastAPI + uvicorn server lifecycle within the bot's event loop."""

    def __init__(
        self,
        application: Application,
        *,
        host: str = "127.0.0.1",
        port: int = 6185,
        auth_token: str = "",
        cors_origins: list[str] | None = None,
    ) -> None:
        self._application = application
        self._host = host
        self._port = port
        self._auth_token = auth_token
        self._server: uvicorn.Server | None = None
        self._serve_task: asyncio.Task[None] | None = None
        self._node_event_bridge: NodeEventBridge | None = None
        self._init_node_services(application)
        self._fastapi = self._build_fastapi(cors_origins or ["*"])

    def _init_node_services(self, application: Application) -> None:
        """Initialize Gateway-Node protocol services.

        Services live on the ``WebAPIApp`` instance so they outlive individual
        WebSocket connections and can be shared with REST routes. The protocol
        layer is enabled by default; set ``webapi.nodes.enabled: false`` to
        disable the WebSocket endpoint entirely.
        """
        from nahida_bot.db.engine import DatabaseEngine
        from nahida_bot.db.repositories.sqlite_node_token_repo import (
            SQLiteNodeTokenStore,
        )
        from nahida_bot.gateway.services.node_auth import NodeAuthService
        from nahida_bot.gateway.services.desktop_announcement import (
            DesktopAnnouncementService,
        )
        from nahida_bot.gateway.services.node_input_sink import ApplicationNodeInputSink
        from nahida_bot.gateway.services.node_invoker import NodeInvoker
        from nahida_bot.gateway.services.node_registry import NodeRegistry

        cfg = application.settings.webapi.nodes
        engine = getattr(application, "_db_engine", None)
        token_store = (
            SQLiteNodeTokenStore(engine) if isinstance(engine, DatabaseEngine) else None
        )
        self.node_registry = (
            NodeRegistry(
                heartbeat_interval_ms=cfg.heartbeat_interval_ms,
                heartbeat_timeout_ms=cfg.heartbeat_timeout_ms,
            )
            if cfg.enabled
            else None
        )
        self.node_auth = (
            NodeAuthService(
                store=token_store,
                pairing_ttl_seconds=cfg.pairing_ttl_seconds,
                default_ttl_seconds=cfg.node_token_ttl_seconds,
            )
            if cfg.enabled
            else None
        )
        self.node_invoker = (
            NodeInvoker(
                self.node_registry,  # type: ignore[arg-type]
                input_sink=ApplicationNodeInputSink(application),
            )
            if cfg.enabled
            else None
        )
        self.desktop_announcement_service = (
            DesktopAnnouncementService(self.node_registry, self.node_invoker)
            if self.node_registry is not None and self.node_invoker is not None
            else None
        )
        application.desktop_announcement_service = self.desktop_announcement_service

    @property
    def fastapi_app(self) -> FastAPI:
        return self._fastapi

    def _build_fastapi(self, cors_origins: list[str]) -> FastAPI:
        app = FastAPI(
            title=self._application.settings.app_name,
            docs_url="/docs" if self._application.settings.debug else None,
            redoc_url=None,
        )

        app.state.application = self._application
        app.state.auth_token = self._auth_token
        app.state.webui_auth = WebUIAuthService(self._application.settings.webui.auth)
        app.state.node_registry = self.node_registry
        app.state.node_auth = self.node_auth
        app.state.node_invoker = self.node_invoker
        app.state.speech_service = getattr(self._application, "speech_service", None)
        app.state.speech_artifact_store = getattr(
            self._application, "speech_artifact_store", None
        )
        app.state.speech_config = self._application.settings.webapi.speech

        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["*"],
        )

        app.add_middleware(_RequestLoggingMiddleware)

        register_error_handlers(app)

        from nahida_bot.gateway.routes.auth import router as auth_router
        from nahida_bot.gateway.routes.config import router as config_router
        from nahida_bot.gateway.routes.cron import router as cron_router
        from nahida_bot.gateway.routes.events import router as events_router
        from nahida_bot.gateway.routes.files import router as files_router
        from nahida_bot.gateway.routes.health import router as health_router
        from nahida_bot.gateway.routes.kb import router as kb_router
        from nahida_bot.gateway.routes.identity import router as identity_router
        from nahida_bot.gateway.routes.logs import router as logs_router
        from nahida_bot.gateway.routes.messages import router as messages_router
        from nahida_bot.gateway.routes.memory import router as memory_router
        from nahida_bot.gateway.routes.plugins import router as plugins_router
        from nahida_bot.gateway.routes.processes import router as processes_router
        from nahida_bot.gateway.routes.sessions import router as sessions_router
        from nahida_bot.gateway.routes.skills import router as skills_router
        from nahida_bot.gateway.routes.speech import (
            media_router as speech_media_router,
            router as speech_router,
        )
        from nahida_bot.gateway.routes.status import router as status_router
        from nahida_bot.gateway.routes.tokens import router as tokens_router
        from nahida_bot.gateway.routes.webui import (
            bootstrap_router,
            system_router as webui_system_router,
        )

        # Unauthenticated routes
        app.include_router(health_router)
        app.include_router(bootstrap_router)
        app.include_router(auth_router)

        # Node WebSocket endpoint is unauthenticated at the HTTP layer; the
        # WebSocket handshake performs its own token verification.
        if self.node_registry is not None:
            from nahida_bot.gateway.node_protocol.routes import router as node_ws_router

            app.include_router(node_ws_router)

        @app.api_route(
            "/webhooks/{path:path}",
            methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        )
        async def plugin_webhook_dispatch(path: str, request: Request) -> Response:
            webhost = getattr(self._application, "webhost_service", None)
            if webhost is None:
                return Response(
                    content="Webhook host is not available",
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            client_host = request.client.host if request.client is not None else ""
            webhook_response = await webhost.dispatch(
                path=path,
                method=request.method,
                headers={k.lower(): v for k, v in request.headers.items()},
                query=dict(request.query_params),
                body=await request.body(),
                client_host=client_host,
            )
            return Response(
                content=webhook_response.body,
                status_code=webhook_response.status_code,
                headers=webhook_response.headers,
            )

        # Authenticated routes
        app.include_router(webui_system_router, dependencies=[Depends(require_token)])
        app.include_router(status_router, dependencies=[Depends(require_token)])
        app.include_router(config_router, dependencies=[Depends(require_token)])
        app.include_router(sessions_router, dependencies=[Depends(require_token)])
        app.include_router(messages_router, dependencies=[Depends(require_token)])
        app.include_router(cron_router, dependencies=[Depends(require_token)])
        app.include_router(files_router, dependencies=[Depends(require_token)])
        app.include_router(logs_router, dependencies=[Depends(require_token)])
        app.include_router(events_router, dependencies=[Depends(require_token)])
        app.include_router(tokens_router, dependencies=[Depends(require_token)])
        app.include_router(memory_router, dependencies=[Depends(require_token)])
        app.include_router(plugins_router, dependencies=[Depends(require_token)])
        app.include_router(processes_router, dependencies=[Depends(require_token)])
        app.include_router(skills_router, dependencies=[Depends(require_token)])
        app.include_router(kb_router, dependencies=[Depends(require_token)])
        app.include_router(identity_router, dependencies=[Depends(require_token)])

        # Speech synthesis + cached media download (Desktop TTS Part B).
        # Admin-gated; Desktop reuses the admin bearer used for pairing.
        # Routes are always mounted so misconfigured clients see a clear 503
        # from _get_services instead of a generic 404/405.
        app.include_router(speech_router, dependencies=[Depends(require_token)])
        app.include_router(speech_media_router, dependencies=[Depends(require_token)])

        if self.node_registry is not None:
            from nahida_bot.gateway.routes.nodes import (
                public_router as nodes_public_router,
                router as nodes_router,
            )

            # Admin-gated node management routes.
            app.include_router(nodes_router, dependencies=[Depends(require_token)])
            # Public pairing completion: the pairing token itself is the
            # credential, so admin auth is intentionally not required here.
            app.include_router(nodes_public_router)

        # Mount WebUI static assets if build output exists
        self._mount_webui(app)

        return app

    def _mount_webui(self, app: FastAPI) -> None:
        webui_dir: Path | None = None
        for p in _WEBUI_SEARCH_PATHS:
            if (p / "index.html").exists():
                webui_dir = p
                break

        if webui_dir is None:
            return

        assets_dir = webui_dir / "assets"
        if assets_dir.is_dir():
            app.mount(
                "/assets",
                StaticFiles(directory=str(assets_dir)),
                name="webui-assets",
            )

        index_html = webui_dir / "index.html"
        webui_root = webui_dir.resolve()

        @app.get("/")
        async def webui_index() -> FileResponse:
            return FileResponse(str(index_html))

        @app.get("/{path:path}")
        async def webui_spa(path: str) -> FileResponse:
            if path == "api" or path.startswith("api/"):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="API route not found",
                )

            candidate = (webui_dir / path).resolve()
            try:
                candidate.relative_to(webui_root)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Static asset not found",
                ) from None

            if candidate.is_file():
                return FileResponse(str(candidate))

            return FileResponse(str(index_html))

        logger.info("webui.mounted", path=str(webui_dir))

    async def start(self) -> None:
        config = uvicorn.Config(
            app=self._fastapi,
            host=self._host,
            port=self._port,
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(config)
        self._server = server

        # Fire-and-forget serve; poll server.started to confirm bind.
        self._serve_task = asyncio.create_task(server.serve())

        for _ in range(50):  # 5 s total
            if server.started:
                break
            if self._serve_task.done():
                exc = self._serve_task.exception()
                raise RuntimeError(
                    f"WebAPI failed to start on {self._host}:{self._port}"
                ) from exc
            await asyncio.sleep(0.1)
        else:
            self._serve_task.cancel()
            raise RuntimeError(
                f"WebAPI timed out starting on {self._host}:{self._port}"
            )

        # Initialize SSE event broadcaster
        from nahida_bot.gateway.services.event_broadcaster import EventBroadcaster

        broadcaster = EventBroadcaster(self._application)
        self._fastapi.state.event_broadcaster = broadcaster
        await broadcaster.start()

        # Initialize node event bridge (forwards core events to online nodes).
        if self.node_registry is not None:
            from nahida_bot.gateway.services.node_event_bridge import NodeEventBridge

            self._node_event_bridge = NodeEventBridge(
                self._application, self.node_registry
            )
            await self._node_event_bridge.start()

        logger.info("webapi.started", host=self._host, port=self._port)

    async def stop(self) -> None:
        # Stop node event bridge
        if self._node_event_bridge is not None:
            await self._node_event_bridge.stop()
            self._node_event_bridge = None

        # Close shared speech service (provider HTTP clients).
        svc = getattr(self._application, "speech_service", None)
        if svc is not None:
            try:
                await svc.close()
            except Exception:  # noqa: BLE001
                logger.warning("webapi.speech_close_failed")
        store = getattr(self._application, "speech_artifact_store", None)
        if store is not None:
            try:
                await store.close()
            except Exception:  # noqa: BLE001
                logger.warning("webapi.speech_artifact_store_close_failed")

        # Stop SSE broadcaster
        broadcaster = getattr(self._fastapi.state, "event_broadcaster", None)
        if broadcaster is not None:
            await broadcaster.stop()

        if self._server is not None:
            self._server.should_exit = True
        if self._serve_task is not None:
            try:
                await asyncio.wait_for(self._serve_task, timeout=5.0)
            except TimeoutError:
                self._serve_task.cancel()
                try:
                    await self._serve_task
                except (asyncio.CancelledError, SystemExit):
                    pass
            self._serve_task = None
        self._server = None
        logger.info("webapi.stopped")
