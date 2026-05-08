"""Adapt MCP tool definitions to nahida-bot ToolEntry objects."""

from __future__ import annotations

import asyncio
import re
from typing import Any, Awaitable, Callable

import structlog

from nahida_bot.plugins.mcp.connection import MCPServerConnection

logger = structlog.get_logger(__name__)

_UNSAFE_CHAR_RE = re.compile(r"[^A-Za-z0-9_-]")
_SEPARATOR = "__"
_MAX_PREFIX = 30
_MAX_TOTAL = 64


def _sanitize_fragment(name: str, max_len: int) -> str:
    """Replace unsafe chars with ``-`` and truncate."""
    sanitized = _UNSAFE_CHAR_RE.sub("-", name)
    return sanitized[:max_len]


def build_safe_tool_name(
    server_name: str,
    tool_name: str,
    reserved_names: set[str] | None = None,
) -> str:
    """Build a provider-safe, collision-free tool name.

    Pattern: ``{server}__{tool}`` (double underscore separator).
    On collision with *reserved_names*, appends ``-2``, ``-3``, ...
    """
    safe_server = _sanitize_fragment(server_name, _MAX_PREFIX) or "server"
    safe_tool = _sanitize_fragment(tool_name, _MAX_TOTAL) or "tool"

    # Truncate tool part to fit within MAX_TOTAL
    prefix_len = len(f"{safe_server}{_SEPARATOR}")
    max_tool_len = _MAX_TOTAL - prefix_len
    if max_tool_len < 1:
        max_tool_len = 1
    safe_tool = safe_tool[:max_tool_len]

    candidate = f"{safe_server}{_SEPARATOR}{safe_tool}"

    if reserved_names and candidate in reserved_names:
        base = candidate
        suffix = 2
        while f"{base}-{suffix}" in reserved_names and suffix < 100:
            suffix += 1
        candidate = f"{base}-{suffix}"

    return candidate


def serialize_mcp_result(result: Any) -> str:
    """Serialize an MCP CallToolResult to a string.

    Falls back through: content -> structuredContent -> status summary.
    """
    parts: list[str] = []
    is_error = getattr(result, "isError", False)

    # Primary: content array
    content = getattr(result, "content", None)
    if content:
        for item in content:
            content_type = getattr(item, "type", "")
            if content_type == "text":
                parts.append(item.text)
            elif content_type == "image":
                data = getattr(item, "data", "")
                mime = getattr(item, "mimeType", "image/png")
                size = len(data) if data else 0
                parts.append(f"[Image: {mime}, ~{size} chars base64]")
            elif content_type == "resource":
                resource = getattr(item, "resource", None)
                if resource is not None:
                    text = getattr(resource, "text", None)
                    if text is not None:
                        parts.append(text)
                    else:
                        parts.append(f"[Resource: {resource}]")
                else:
                    parts.append(f"[EmbeddedResource: {item}]")
            else:
                parts.append(str(item))

    # Fallback: structuredContent
    if not parts:
        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            parts.append(str(structured))

    # Final fallback: status summary
    if not parts:
        status = "error" if is_error else "ok"
        parts.append(f"[MCP result: {status}]")

    text = "\n".join(parts)
    if is_error:
        return f"[MCP Error] {text}"
    return text


def create_tool_handler(
    connection: MCPServerConnection,
    tool_name: str,
    timeout: float,
) -> Callable[..., Awaitable[str]]:
    """Create a handler closure for an MCP tool.

    The closure matches the ``Callable[..., Awaitable[str]]`` signature
    expected by ``ToolRegistry.register()``.
    """

    async def handler(**kwargs: Any) -> str:
        try:
            result = await asyncio.wait_for(
                connection.call_tool(tool_name, kwargs),
                timeout=timeout,
            )
            return serialize_mcp_result(result)
        except asyncio.TimeoutError:
            return (
                f"[MCP Error] Tool '{tool_name}' timed out "
                f"after {timeout}s on server '{connection.server_key}'"
            )
        except Exception as exc:
            logger.warning(
                "mcp.tool_call_failed",
                tool=tool_name,
                server=connection.server_key,
                error=str(exc),
            )
            reconnected = await connection.reconnect()
            if reconnected:
                try:
                    result = await asyncio.wait_for(
                        connection.call_tool(tool_name, kwargs),
                        timeout=timeout,
                    )
                    return serialize_mcp_result(result)
                except Exception as retry_exc:
                    return (
                        f"[MCP Error] Tool '{tool_name}' failed after "
                        f"reconnect on server '{connection.server_key}': "
                        f"{retry_exc}"
                    )
            return (
                f"[MCP Error] Tool '{tool_name}' failed on server "
                f"'{connection.server_key}' and reconnect failed: {exc}"
            )

    return handler


def mcp_tool_to_entry(
    connection: MCPServerConnection,
    namespace: str,
    mcp_tool: Any,
    timeout: float,
    reserved_names: set[str] | None = None,
) -> tuple[str, str, dict[str, Any], Callable[..., Awaitable[str]]]:
    """Convert an MCP Tool object to arguments for ``api.register_tool()``.

    Returns:
        (name, description, parameters, handler) tuple.
    """
    namespaced_name = build_safe_tool_name(namespace, mcp_tool.name, reserved_names)
    description = mcp_tool.description or ""
    parameters = mcp_tool.inputSchema or {
        "type": "object",
        "properties": {},
    }
    handler = create_tool_handler(connection, mcp_tool.name, timeout)

    return namespaced_name, description, parameters, handler
