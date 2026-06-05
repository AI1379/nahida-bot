"""MCP integration plugin for nahida-bot."""

from __future__ import annotations

from typing import Any

import structlog

from nahida_bot.plugins.base import Plugin

from nahida_bot.plugins.mcp.config import (
    MCPServerConfig,
    parse_mcp_config,
    server_config_from_dict,
    server_config_to_dict,
)
from nahida_bot.plugins.mcp.connection import MCPServerConnection
from nahida_bot.plugins.mcp.tool_adapter import mcp_tool_to_entry

logger = structlog.get_logger(__name__)

PLUGIN_ID = "mcp"

# Key prefix used in plugin_data for dynamic server configs.
_DATA_PREFIX = "server:"


class MCPPlugin(Plugin):
    """Connects to configured MCP servers and registers their tools.

    Servers can come from two sources:
      * **Static** — defined in ``config.yaml`` under the ``mcp:`` key.
      * **Dynamic** — added at runtime via ``add_server()`` and persisted
        in the plugin data store.

    When a server key exists in both sources, the static definition wins.
    """

    def __init__(self, api: Any, manifest: Any) -> None:
        super().__init__(api, manifest)
        self._connections: dict[str, MCPServerConnection] = {}
        self._tool_names_by_server: dict[str, list[str]] = {}
        self._static_server_keys: set[str] = set()

    # ── Lifecycle ──────────────────────────────────────

    async def on_load(self) -> None:
        static_config = parse_mcp_config(self.manifest.config)
        self._static_server_keys = set(static_config.servers.keys())

        # Load dynamic servers from plugin data store.
        dynamic_servers = await self._load_dynamic_servers()
        dynamic_servers = self._filter_allowed_dynamic_servers(
            dynamic_servers,
            static_config,
        )

        # Merge: static wins on conflict.
        merged: dict[str, MCPServerConfig] = {
            **dynamic_servers,
            **static_config.servers,
        }

        if not merged:
            logger.info("mcp.no_servers_configured")
        else:
            for server_key, server_config in merged.items():
                if not server_config.enabled:
                    logger.info("mcp.server_disabled", server=server_key)
                    continue

                await self._connect_and_register(server_key, server_config)

            total = sum(len(v) for v in self._tool_names_by_server.values())
            logger.info(
                "mcp.loaded",
                servers=len(self._connections),
                tools=total,
                static=len(static_config.servers),
                dynamic=len(dynamic_servers),
            )

        # Register LLM-callable management tools (always, even with no servers).
        self._register_management_tools()

    async def on_unload(self) -> None:
        for connection in self._connections.values():
            try:
                await connection.disconnect()
            except Exception:
                logger.debug("mcp.disconnect_error", server=connection.server_key)
        self._connections.clear()
        self._tool_names_by_server.clear()
        self._static_server_keys.clear()
        # Tool unregistration is handled automatically by
        # PluginManager.disable() calling unregister_by_plugin("mcp").

    # ── Dynamic Server Management ─────────────────────

    async def add_server(
        self, server_key: str, config: MCPServerConfig
    ) -> dict[str, Any]:
        """Add a dynamic MCP server, persist it, and connect.

        Returns a summary dict with the outcome.
        """
        mcp_config = parse_mcp_config(self.manifest.config)
        allowed, reason = self._validate_dynamic_server_config(
            server_key,
            config,
            mcp_config,
        )
        if not allowed:
            return {
                "status": "policy_denied",
                "server_key": server_key,
                "message": reason,
            }

        # Persist.
        await self.api.plugin_data_set(
            f"{_DATA_PREFIX}{server_key}", server_config_to_dict(config)
        )

        if server_key in self._static_server_keys:
            return {
                "status": "shadowed",
                "server_key": server_key,
                "message": "A static server with this key already exists in config.yaml; the dynamic entry is saved but not connected.",
            }

        await self._disconnect_and_unregister(server_key)

        if not config.enabled:
            return {
                "status": "disabled",
                "server_key": server_key,
                "message": "Server saved but not connected (enabled=false).",
            }

        await self._connect_and_register(server_key, config)
        connected = server_key in self._connections
        return {
            "status": "connected" if connected else "connect_failed",
            "server_key": server_key,
            "tool_count": len(self._tool_names_by_server.get(server_key, [])),
        }

    async def remove_server(self, server_key: str) -> dict[str, Any]:
        """Remove a dynamic server: disconnect, unregister tools, delete data."""
        if server_key in self._static_server_keys:
            return {
                "status": "error",
                "message": f"Cannot remove static server '{server_key}'. Remove it from config.yaml instead.",
            }

        tool_count = len(self._tool_names_by_server.get(server_key, []))
        had_connection = server_key in self._connections
        await self._disconnect_and_unregister(server_key)
        deleted = await self.api.plugin_data_delete(f"{_DATA_PREFIX}{server_key}")

        return {
            "status": "removed"
            if deleted or had_connection or tool_count
            else "not_found",
            "server_key": server_key,
            "tools_unregistered": tool_count,
        }

    async def list_servers(self) -> list[dict[str, Any]]:
        """List all servers (static + dynamic) with connection status."""
        static_config = parse_mcp_config(self.manifest.config)
        dynamic_data = await self.api.plugin_data_list(prefix=_DATA_PREFIX)

        result: list[dict[str, Any]] = []

        for key, cfg in static_config.servers.items():
            result.append(
                {
                    "server_key": key,
                    "source": "static",
                    "transport": cfg.transport,
                    "enabled": cfg.enabled,
                    "connected": key in self._connections,
                    "tool_count": len(self._tool_names_by_server.get(key, [])),
                }
            )

        for key, value in dynamic_data.items():
            server_key = key.removeprefix(_DATA_PREFIX)
            if server_key in static_config.servers:
                continue  # already listed as static
            try:
                cfg = server_config_from_dict(value)
                result.append(
                    {
                        "server_key": server_key,
                        "source": "dynamic",
                        "transport": cfg.transport,
                        "enabled": cfg.enabled,
                        "connected": server_key in self._connections,
                        "tool_count": len(
                            self._tool_names_by_server.get(server_key, [])
                        ),
                    }
                )
            except Exception:
                logger.debug("mcp.dynamic_server_parse_error", key=key)

        return result

    async def reload_server(self, server_key: str) -> dict[str, Any]:
        """Reconnect a specific server."""
        if (
            server_key not in self._connections
            and server_key not in self._tool_names_by_server
        ):
            config = await self._resolve_server_config(server_key)
            if config is None:
                return {
                    "status": "not_found",
                    "server_key": server_key,
                    "message": "Server configuration was not found.",
                }
        else:
            config = None

        if server_key in self._connections or server_key in self._tool_names_by_server:
            # Disconnect and unregister existing tools.
            await self._disconnect_and_unregister(server_key)

        # Re-resolve config (static or dynamic).
        config = config or await self._resolve_server_config(server_key)
        if config is None:
            return {
                "status": "config_not_found",
                "server_key": server_key,
                "message": "Could not find server configuration.",
            }

        if server_key not in self._static_server_keys:
            mcp_config = parse_mcp_config(self.manifest.config)
            allowed, reason = self._validate_dynamic_server_config(
                server_key,
                config,
                mcp_config,
            )
            if not allowed:
                return {
                    "status": "policy_denied",
                    "server_key": server_key,
                    "message": reason,
                }

        if not config.enabled:
            return {
                "status": "disabled",
                "server_key": server_key,
                "message": "Server is disabled; skipped reconnection.",
            }

        await self._connect_and_register(server_key, config)
        connected = server_key in self._connections
        return {
            "status": "reconnected" if connected else "reconnect_failed",
            "server_key": server_key,
            "tool_count": len(self._tool_names_by_server.get(server_key, [])),
        }

    # ── Internal ───────────────────────────────────────

    async def _load_dynamic_servers(self) -> dict[str, MCPServerConfig]:
        """Load dynamic server configs from the plugin data store."""
        data = await self.api.plugin_data_list(prefix=_DATA_PREFIX)
        servers: dict[str, MCPServerConfig] = {}
        for key, value in data.items():
            server_key = key.removeprefix(_DATA_PREFIX)
            try:
                servers[server_key] = server_config_from_dict(value)
            except Exception:
                logger.warning("mcp.dynamic_server_parse_error", key=key)
        return servers

    async def _resolve_server_config(self, server_key: str) -> MCPServerConfig | None:
        """Resolve config for a server key from static or dynamic source."""
        static_config = parse_mcp_config(self.manifest.config)
        if server_key in static_config.servers:
            return static_config.servers[server_key]

        data = await self.api.plugin_data_get(f"{_DATA_PREFIX}{server_key}")
        if data is not None:
            try:
                return server_config_from_dict(data)
            except Exception:
                pass
        return None

    def _filter_allowed_dynamic_servers(
        self,
        servers: dict[str, MCPServerConfig],
        mcp_config: Any,
    ) -> dict[str, MCPServerConfig]:
        """Drop persisted dynamic servers that are no longer allowed by policy."""
        allowed_servers: dict[str, MCPServerConfig] = {}
        for server_key, server_config in servers.items():
            allowed, reason = self._validate_dynamic_server_config(
                server_key,
                server_config,
                mcp_config,
            )
            if allowed:
                allowed_servers[server_key] = server_config
            else:
                logger.warning(
                    "mcp.dynamic_server_policy_denied",
                    server=server_key,
                    reason=reason,
                )
        return allowed_servers

    def _validate_dynamic_server_config(
        self,
        server_key: str,
        server_config: MCPServerConfig,
        mcp_config: Any,
    ) -> tuple[bool, str]:
        """Validate dynamic server targets against manifest-configured allowlists."""
        if server_config.transport == "stdio":
            allowed_commands = set(mcp_config.allowed_dynamic_stdio_commands)
            command = server_config.command
            if not command:
                return False, "Dynamic stdio servers require a command."
            if command not in allowed_commands:
                return (
                    False,
                    f"Dynamic stdio command '{command}' is not allowed for server '{server_key}'.",
                )
            return True, ""

        allowed_prefixes = tuple(mcp_config.allowed_dynamic_url_prefixes)
        url = server_config.url
        if not url:
            return False, "Dynamic URL-based servers require a URL."
        if not allowed_prefixes or not url.startswith(allowed_prefixes):
            return (
                False,
                f"Dynamic server URL '{url}' is not allowed for server '{server_key}'.",
            )
        return True, ""

    async def _disconnect_and_unregister(self, server_key: str) -> None:
        """Disconnect a server and unregister its tools."""
        connection = self._connections.pop(server_key, None)
        if connection is not None:
            try:
                await connection.disconnect()
            except Exception:
                logger.debug("mcp.disconnect_error", server=server_key)

        tool_names = self._tool_names_by_server.pop(server_key, [])
        for tool_name in tool_names:
            try:
                self.api.unregister_tool(tool_name)
            except Exception:
                logger.debug("mcp.tool_unregister_failed", tool=tool_name)

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

    def _register_management_tools(self) -> None:
        """Register LLM-callable tools for MCP server management."""
        self.api.register_tool(
            "mcp_add_server",
            "Add a new MCP server connection dynamically. The server configuration is persisted and will be reconnected on restart.",
            {
                "type": "object",
                "properties": {
                    "server_key": {
                        "type": "string",
                        "description": "Unique identifier for this server (alphanumeric, dashes allowed)",
                    },
                    "transport": {
                        "type": "string",
                        "enum": ["stdio", "sse", "streamable-http"],
                        "description": "Transport protocol",
                    },
                    "command": {
                        "type": "string",
                        "description": "Executable command (for stdio transport)",
                    },
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Command arguments (for stdio transport)",
                    },
                    "url": {
                        "type": "string",
                        "description": "Server URL (for sse/streamable-http transport)",
                    },
                    "namespace": {
                        "type": "string",
                        "description": "Tool name prefix (defaults to server_key)",
                    },
                    "enabled": {
                        "type": "boolean",
                        "description": "Whether to connect immediately (default: true)",
                    },
                },
                "required": ["server_key", "transport"],
            },
            self._tool_add_server,
        )
        self.api.register_tool(
            "mcp_remove_server",
            "Remove a dynamically added MCP server. Disconnects and deletes persisted configuration. Cannot remove static servers defined in config.yaml.",
            {
                "type": "object",
                "properties": {
                    "server_key": {
                        "type": "string",
                        "description": "The server to remove",
                    },
                },
                "required": ["server_key"],
            },
            self._tool_remove_server,
        )
        self.api.register_tool(
            "mcp_list_servers",
            "List all MCP servers (both static from config and dynamically added) with their connection status and tool counts.",
            {"type": "object", "properties": {}},
            self._tool_list_servers,
        )
        self.api.register_tool(
            "mcp_reload_server",
            "Reconnect a specific MCP server. Disconnects, re-reads configuration, and reconnects. Useful when a server's tools have changed.",
            {
                "type": "object",
                "properties": {
                    "server_key": {
                        "type": "string",
                        "description": "The server to reload",
                    },
                },
                "required": ["server_key"],
            },
            self._tool_reload_server,
        )

    # ── Tool Handlers ──────────────────────────────────

    async def _tool_add_server(self, **kwargs: Any) -> str:
        server_key = kwargs["server_key"]
        transport = kwargs["transport"]
        config = MCPServerConfig(
            transport=transport,
            command=kwargs.get("command", ""),
            args=kwargs.get("args", []),
            env={},
            url=kwargs.get("url", ""),
            headers={},
            namespace=kwargs.get("namespace", ""),
            enabled=kwargs.get("enabled", True),
        )
        result = await self.add_server(server_key, config)
        import json

        return json.dumps(result, ensure_ascii=False)

    async def _tool_remove_server(self, **kwargs: Any) -> str:
        server_key = kwargs["server_key"]
        result = await self.remove_server(server_key)
        import json

        return json.dumps(result, ensure_ascii=False)

    async def _tool_list_servers(self, **kwargs: Any) -> str:
        servers = await self.list_servers()
        import json

        return json.dumps({"servers": servers}, ensure_ascii=False)

    async def _tool_reload_server(self, **kwargs: Any) -> str:
        server_key = kwargs["server_key"]
        result = await self.reload_server(server_key)
        import json

        return json.dumps(result, ensure_ascii=False)
