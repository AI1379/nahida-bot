"""WebAPI application: FastAPI + uvicorn lifecycle."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog
import uvicorn
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from nahida_bot.gateway.auth import require_token
from nahida_bot.gateway.errors import register_error_handlers

if TYPE_CHECKING:
    from nahida_bot.core.app import Application

logger = structlog.get_logger(__name__)


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
            allow_methods=["GET", "POST"],
            allow_headers=["*"],
        )

        register_error_handlers(app)

        from nahida_bot.gateway.routes.cron import router as cron_router
        from nahida_bot.gateway.routes.health import router as health_router
        from nahida_bot.gateway.routes.messages import router as messages_router
        from nahida_bot.gateway.routes.sessions import router as sessions_router

        app.include_router(health_router)
        app.include_router(sessions_router, dependencies=[Depends(require_token)])
        app.include_router(messages_router, dependencies=[Depends(require_token)])
        app.include_router(cron_router, dependencies=[Depends(require_token)])

        return app

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
