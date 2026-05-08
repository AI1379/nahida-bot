"""MCP server connection management."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

from nahida_bot.plugins.mcp.config import MCPServerConfig

if TYPE_CHECKING:
    from mcp import ClientSession

logger = structlog.get_logger(__name__)


class MCPServerConnection:
    """Manages a single MCP server connection (stdio / sse / streamable-http)."""

    def __init__(self, server_key: str, config: MCPServerConfig) -> None:
        self._server_key = server_key
        self._config = config
        self._session: ClientSession | None = None
        self._transport_cm: Any = None
        self._session_cm: Any = None
        self._connected = False

    @property
    def server_key(self) -> str:
        return self._server_key

    @property
    def is_connected(self) -> bool:
        return self._connected and self._session is not None

    async def connect(self) -> None:
        """Open transport, create session, and initialize."""
        from mcp import ClientSession

        try:
            read_stream, write_stream = await self._open_transport()
        except Exception:
            logger.exception(
                "mcp.transport_open_failed",
                server=self._server_key,
                transport=self._config.transport,
            )
            raise

        self._session_cm = ClientSession(read_stream, write_stream)
        try:
            session = await self._session_cm.__aenter__()
        except Exception:
            logger.exception("mcp.session_create_failed", server=self._server_key)
            await self._close_transport()
            raise
        self._session = session

        try:
            await session.initialize()
        except Exception:
            logger.exception("mcp.session_initialize_failed", server=self._server_key)
            await self._close_session()
            await self._close_transport()
            raise

        self._connected = True
        logger.info(
            "mcp.server_connected",
            server=self._server_key,
            transport=self._config.transport,
        )

    async def disconnect(self) -> None:
        """Tear down session and transport."""
        if not self._connected:
            return

        self._connected = False
        await self._close_session()
        await self._close_transport()
        logger.info("mcp.server_disconnected", server=self._server_key)

    async def list_tools(self) -> list[Any]:
        """List all tools exposed by this MCP server (handles pagination)."""
        if self._session is None:
            raise RuntimeError(f"MCP server {self._server_key} is not connected")
        all_tools: list[Any] = []
        cursor: str | None = None
        while True:
            result = await self._session.list_tools(cursor=cursor)
            all_tools.extend(result.tools)
            next_cursor = getattr(result, "nextCursor", None)
            if not next_cursor:
                break
            cursor = next_cursor
        return all_tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Call a tool on this MCP server."""
        if self._session is None:
            raise RuntimeError(f"MCP server {self._server_key} is not connected")
        return await self._session.call_tool(name, arguments=arguments)

    async def reconnect(self) -> bool:
        """Disconnect and reconnect with retry.

        Returns True if reconnection succeeded.
        """
        await self.disconnect()

        for attempt in range(self._config.reconnect_attempts):
            try:
                await self.connect()
                return True
            except Exception:
                logger.warning(
                    "mcp.reconnect_attempt_failed",
                    server=self._server_key,
                    attempt=attempt + 1,
                    max_attempts=self._config.reconnect_attempts,
                )
                if attempt < self._config.reconnect_attempts - 1:
                    delay = self._config.reconnect_delay_seconds * (attempt + 1)
                    await asyncio.sleep(delay)

        logger.error(
            "mcp.reconnect_exhausted",
            server=self._server_key,
            attempts=self._config.reconnect_attempts,
        )
        return False

    # ── Transport helpers ──────────────────────────────────

    async def _open_transport(self) -> tuple[Any, Any]:
        """Open the configured transport and return (read, write) streams."""
        transport = self._config.transport

        if transport == "stdio":
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client

            params = StdioServerParameters(
                command=self._config.command,
                args=self._config.args,
                env=self._config.env or None,
            )
            self._transport_cm = stdio_client(params)
            read, write = await self._transport_cm.__aenter__()

        elif transport == "sse":
            from mcp.client.sse import sse_client

            self._transport_cm = sse_client(
                url=self._config.url,
                headers=self._config.headers or None,
            )
            read, write = await self._transport_cm.__aenter__()

        elif transport == "streamable-http":
            from mcp.client.streamable_http import streamable_http_client

            self._transport_cm = streamable_http_client(
                url=self._config.url,
            )
            result = await self._transport_cm.__aenter__()
            # streamable_http_client yields (read, write, get_session_id)
            read, write = result[0], result[1]

        else:
            raise ValueError(
                f"Unknown MCP transport: {transport!r} (server={self._server_key})"
            )

        return read, write

    async def _close_session(self) -> None:
        if self._session_cm is not None:
            try:
                await self._session_cm.__aexit__(None, None, None)
            except Exception:
                logger.debug("mcp.session_close_error", server=self._server_key)
            self._session_cm = None
            self._session = None

    async def _close_transport(self) -> None:
        if self._transport_cm is not None:
            try:
                await self._transport_cm.__aexit__(None, None, None)
            except Exception:
                logger.debug("mcp.transport_close_error", server=self._server_key)
            self._transport_cm = None
