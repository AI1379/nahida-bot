"""MCP integration plugin for nahida-bot."""

from __future__ import annotations

from typing import Any

import structlog

from nahida_bot.plugins.base import Plugin

from nahida_bot.plugins.mcp.config import MCPServerConfig, parse_mcp_config
from nahida_bot.plugins.mcp.connection import MCPServerConnection
from nahida_bot.plugins.mcp.tool_adapter import mcp_tool_to_entry

logger = structlog.get_logger(__name__)

PLUGIN_ID = "mcp"


class MCPPlugin(Plugin):
    """Connects to configured MCP servers and registers their tools."""

    def __init__(self, api: Any, manifest: Any) -> None:
        super().__init__(api, manifest)
        self._connections: dict[str, MCPServerConnection] = {}
        self._tool_names_by_server: dict[str, list[str]] = {}

    # ── Lifecycle ──────────────────────────────────────

    async def on_load(self) -> None:
        config = parse_mcp_config(self.manifest.config)

        if not config.servers:
            logger.info("mcp.no_servers_configured")
            return

        for server_key, server_config in config.servers.items():
            if not server_config.enabled:
                logger.info(
                    "mcp.server_disabled",
                    server=server_key,
                )
                continue

            await self._connect_and_register(server_key, server_config)

        total = sum(len(v) for v in self._tool_names_by_server.values())
        logger.info(
            "mcp.loaded",
            servers=len(self._connections),
            tools=total,
        )

    async def on_unload(self) -> None:
        for connection in self._connections.values():
            try:
                await connection.disconnect()
            except Exception:
                logger.debug("mcp.disconnect_error", server=connection.server_key)
        self._connections.clear()
        self._tool_names_by_server.clear()
        # Tool unregistration is handled automatically by
        # PluginManager.disable() calling unregister_by_plugin("mcp").

    # ── Internal ───────────────────────────────────────

    async def _connect_and_register(
        self,
        server_key: str,
        server_config: MCPServerConfig,
    ) -> None:
        """Connect to a single MCP server and register its tools."""
        connection = MCPServerConnection(server_key, server_config)
        try:
            await connection.connect()
        except Exception:
            logger.warning(
                "mcp.server_connect_failed",
                server=server_key,
                transport=server_config.transport,
            )
            return

        self._connections[server_key] = connection

        try:
            tools = await connection.list_tools()
        except Exception:
            logger.warning("mcp.list_tools_failed", server=server_key)
            return

        namespace = server_config.namespace or server_key
        registered: list[str] = []

        # Collect already-registered names to avoid collisions.
        existing = set(self._tool_names_by_server.get(server_key, []))
        for names_list in self._tool_names_by_server.values():
            existing.update(names_list)

        for mcp_tool in tools:
            name, description, parameters, handler = mcp_tool_to_entry(
                connection=connection,
                namespace=namespace,
                mcp_tool=mcp_tool,
                timeout=server_config.tool_timeout_seconds,
                reserved_names=existing,
            )
            try:
                self.api.register_tool(name, description, parameters, handler)
                registered.append(name)
            except KeyError:
                logger.warning(
                    "mcp.tool_name_conflict",
                    name=name,
                    server=server_key,
                )

        self._tool_names_by_server[server_key] = registered
        logger.info(
            "mcp.server_tools_registered",
            server=server_key,
            count=len(registered),
        )
