"""WebAPI application: FastAPI + uvicorn lifecycle."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
import uvicorn
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from nahida_bot.gateway.auth import require_token
from nahida_bot.gateway.errors import register_error_handlers

if TYPE_CHECKING:
    from nahida_bot.core.app import Application

logger = structlog.get_logger(__name__)

# Search webui/dist relative to the project root (cwd), not inside the package.
_WEBUI_SEARCH_PATHS = [
    Path.cwd() / "webui" / "dist",
    Path(__file__).resolve().parents[2] / "webui" / "dist",
]


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
        self._fastapi = self._build_fastapi(cors_origins or ["*"])

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

        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["*"],
        )

        register_error_handlers(app)

        from nahida_bot.gateway.routes.config import router as config_router
        from nahida_bot.gateway.routes.cron import router as cron_router
        from nahida_bot.gateway.routes.files import router as files_router
        from nahida_bot.gateway.routes.health import router as health_router
        from nahida_bot.gateway.routes.messages import router as messages_router
        from nahida_bot.gateway.routes.sessions import router as sessions_router
        from nahida_bot.gateway.routes.status import router as status_router
        from nahida_bot.gateway.routes.webui import (
            bootstrap_router,
            system_router as webui_system_router,
        )

        # Unauthenticated routes
        app.include_router(health_router)
        app.include_router(bootstrap_router)

        # Authenticated routes
        app.include_router(webui_system_router, dependencies=[Depends(require_token)])
        app.include_router(status_router, dependencies=[Depends(require_token)])
        app.include_router(config_router, dependencies=[Depends(require_token)])
        app.include_router(sessions_router, dependencies=[Depends(require_token)])
        app.include_router(messages_router, dependencies=[Depends(require_token)])
        app.include_router(cron_router, dependencies=[Depends(require_token)])
        app.include_router(files_router, dependencies=[Depends(require_token)])

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
                "/ui/assets",
                StaticFiles(directory=str(assets_dir)),
                name="webui-assets",
            )

        index_html = webui_dir / "index.html"

        @app.get("/ui/{path:path}")
        async def webui_spa(request: Request, path: str = "") -> FileResponse:
            return FileResponse(str(index_html))

        @app.get("/ui")
        async def webui_index() -> FileResponse:
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

        logger.info("webapi.started", host=self._host, port=self._port)

    async def stop(self) -> None:
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
