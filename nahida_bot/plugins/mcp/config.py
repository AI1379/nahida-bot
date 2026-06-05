"""Configuration models for MCP plugin."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server connection."""

    model_config = ConfigDict(frozen=True, extra="allow")

    transport: Literal["stdio", "sse", "streamable-http"]

    # stdio transport
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)

    # sse / streamable-http transport
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)

    # general
    namespace: str = ""
    enabled: bool = True
    reconnect_attempts: int = 3
    reconnect_delay_seconds: float = 5.0
    tool_timeout_seconds: float = 60.0


class MCPConfig(BaseModel):
    """Top-level MCP plugin configuration."""

    model_config = ConfigDict(frozen=True, extra="allow")

    servers: dict[str, MCPServerConfig] = Field(default_factory=dict)
    allowed_dynamic_stdio_commands: list[str] = Field(default_factory=list)
    allowed_dynamic_url_prefixes: list[str] = Field(default_factory=list)


def parse_mcp_config(raw: dict[str, Any]) -> MCPConfig:
    """Parse raw manifest config dict into MCPConfig."""
    return MCPConfig(**raw)


def server_config_to_dict(config: MCPServerConfig) -> dict[str, Any]:
    """Serialise an MCPServerConfig to a JSON-compatible dict for plugin_data storage."""
    return config.model_dump()


def server_config_from_dict(data: dict[str, Any]) -> MCPServerConfig:
    """Deserialise a dict back into an MCPServerConfig."""
    return MCPServerConfig(**data)
