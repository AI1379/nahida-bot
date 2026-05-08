"""Tests for MCP plugin config models."""

from __future__ import annotations

import pytest

from nahida_bot.plugins.mcp.config import (
    MCPConfig,
    MCPServerConfig,
    parse_mcp_config,
)


class TestMCPServerConfig:
    def test_stdio_config(self) -> None:
        cfg = MCPServerConfig(
            transport="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        )
        assert cfg.transport == "stdio"
        assert cfg.command == "npx"
        assert cfg.args == ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
        assert cfg.enabled is True
        assert cfg.namespace == ""

    def test_sse_config(self) -> None:
        cfg = MCPServerConfig(
            transport="sse",
            url="http://localhost:3001/sse",
            headers={"Authorization": "Bearer token"},
        )
        assert cfg.transport == "sse"
        assert cfg.url == "http://localhost:3001/sse"
        assert cfg.headers == {"Authorization": "Bearer token"}

    def test_streamable_http_config(self) -> None:
        cfg = MCPServerConfig(
            transport="streamable-http",
            url="http://localhost:8080/mcp",
            namespace="remote",
        )
        assert cfg.transport == "streamable-http"
        assert cfg.namespace == "remote"

    def test_defaults(self) -> None:
        cfg = MCPServerConfig(transport="stdio", command="echo")
        assert cfg.reconnect_attempts == 3
        assert cfg.reconnect_delay_seconds == 5.0
        assert cfg.tool_timeout_seconds == 60.0
        assert cfg.enabled is True

    def test_frozen(self) -> None:
        cfg = MCPServerConfig(transport="stdio", command="echo")
        with pytest.raises(Exception):
            cfg.command = "other"  # type: ignore[misc]

    def test_extra_fields_preserved(self) -> None:
        cfg = MCPServerConfig(transport="stdio", command="echo", custom_field="value")  # type: ignore[call-arg]
        assert cfg.custom_field == "value"  # type: ignore[attr-defined]


class TestMCPConfig:
    def test_empty_servers(self) -> None:
        cfg = MCPConfig()
        assert cfg.servers == {}

    def test_multiple_servers(self) -> None:
        cfg = MCPConfig(
            servers={
                "fs": MCPServerConfig(
                    transport="stdio",
                    command="npx",
                    args=["-y", "server-fs"],
                ),
                "search": MCPServerConfig(
                    transport="sse",
                    url="http://localhost:3001/sse",
                ),
            }
        )
        assert len(cfg.servers) == 2
        assert cfg.servers["fs"].transport == "stdio"
        assert cfg.servers["search"].transport == "sse"


class TestParseMcpConfig:
    def test_parse_full_config(self) -> None:
        raw = {
            "servers": {
                "my-server": {
                    "transport": "stdio",
                    "command": "node",
                    "args": ["server.js"],
                    "namespace": "svc",
                    "enabled": False,
                }
            }
        }
        cfg = parse_mcp_config(raw)
        assert "my-server" in cfg.servers
        assert cfg.servers["my-server"].command == "node"
        assert cfg.servers["my-server"].enabled is False

    def test_parse_empty_config(self) -> None:
        cfg = parse_mcp_config({})
        assert cfg.servers == {}

    def test_parse_invalid_transport_raises(self) -> None:
        with pytest.raises(Exception):
            parse_mcp_config({"servers": {"bad": {"transport": "websocket"}}})
