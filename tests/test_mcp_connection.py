"""Tests for MCPServerConnection."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nahida_bot.plugins.mcp.config import MCPServerConfig
from nahida_bot.plugins.mcp.connection import MCPServerConnection


def _stdio_config(**overrides: Any) -> MCPServerConfig:
    return MCPServerConfig(
        transport=overrides.pop("transport", "stdio"),
        command=overrides.pop("command", "echo"),
        args=overrides.pop("args", []),
        **overrides,
    )


def _sse_config(**overrides: Any) -> MCPServerConfig:
    return MCPServerConfig(
        transport=overrides.pop("transport", "sse"),
        url=overrides.pop("url", "http://localhost:3001/sse"),
        **overrides,
    )


def _http_config(**overrides: Any) -> MCPServerConfig:
    return MCPServerConfig(
        transport=overrides.pop("transport", "streamable-http"),
        url=overrides.pop("url", "http://localhost:8080/mcp"),
        **overrides,
    )


class TestMCPServerConnectionInit:
    def test_initial_state(self) -> None:
        conn = MCPServerConnection("test", _stdio_config())
        assert conn.server_key == "test"
        assert not conn.is_connected


def _mock_session_setup() -> tuple[AsyncMock, dict[str, AsyncMock]]:
    """Create mock session and transport context managers."""
    mock_session = AsyncMock()
    mock_session.initialize = AsyncMock()

    mock_session_cm = AsyncMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_cm.__aexit__ = AsyncMock(return_value=None)

    return mock_session, {"ClientSession": MagicMock(return_value=mock_session_cm)}


class TestMCPServerConnectionConnect:
    @pytest.mark.asyncio
    async def test_stdio_connect(self) -> None:
        config = _stdio_config()
        conn = MCPServerConnection("fs", config)

        mock_session, _ = _mock_session_setup()
        mock_transport_cm = AsyncMock()
        mock_transport_cm.__aenter__ = AsyncMock(return_value=("read", "write"))
        mock_transport_cm.__aexit__ = AsyncMock(return_value=None)

        mock_params_cls = MagicMock()

        mock_session_cm = AsyncMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("mcp.ClientSession", return_value=mock_session_cm),
            patch("mcp.StdioServerParameters", mock_params_cls),
            patch("mcp.client.stdio.stdio_client", return_value=mock_transport_cm),
        ):
            await conn.connect()

        assert conn.is_connected
        mock_session.initialize.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sse_connect(self) -> None:
        config = _sse_config()
        conn = MCPServerConnection("search", config)

        mock_session = AsyncMock()
        mock_transport_cm = AsyncMock()
        mock_transport_cm.__aenter__ = AsyncMock(return_value=("r", "w"))
        mock_transport_cm.__aexit__ = AsyncMock(return_value=None)

        mock_session_cm = AsyncMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("mcp.ClientSession", return_value=mock_session_cm),
            patch("mcp.client.sse.sse_client", return_value=mock_transport_cm),
        ):
            await conn.connect()

        assert conn.is_connected

    @pytest.mark.asyncio
    async def test_streamable_http_connect(self) -> None:
        config = _http_config()
        conn = MCPServerConnection("remote", config)

        mock_session = AsyncMock()
        mock_transport_cm = AsyncMock()
        # streamable_http yields (read, write, get_session_id)
        mock_transport_cm.__aenter__ = AsyncMock(return_value=("r", "w", lambda: None))
        mock_transport_cm.__aexit__ = AsyncMock(return_value=None)

        mock_session_cm = AsyncMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("mcp.ClientSession", return_value=mock_session_cm),
            patch(
                "mcp.client.streamable_http.streamable_http_client",
                return_value=mock_transport_cm,
            ),
        ):
            await conn.connect()

        assert conn.is_connected

    @pytest.mark.asyncio
    async def test_connect_failure_cleans_up(self) -> None:
        config = _stdio_config()
        conn = MCPServerConnection("bad", config)

        with patch(
            "mcp.client.stdio.stdio_client",
            side_effect=RuntimeError("spawn failed"),
        ):
            with pytest.raises(RuntimeError, match="spawn failed"):
                await conn.connect()

        assert not conn.is_connected


class TestMCPServerConnectionDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_cleans_up(self) -> None:
        config = _stdio_config()
        conn = MCPServerConnection("test", config)
        conn._connected = True
        conn._session_cm = AsyncMock()
        conn._session_cm.__aexit__ = AsyncMock(return_value=None)
        conn._transport_cm = AsyncMock()
        conn._transport_cm.__aexit__ = AsyncMock(return_value=None)

        await conn.disconnect()

        assert not conn.is_connected
        assert conn._session_cm is None
        assert conn._transport_cm is None

    @pytest.mark.asyncio
    async def test_disconnect_when_not_connected(self) -> None:
        conn = MCPServerConnection("test", _stdio_config())
        await conn.disconnect()  # Should not raise


class TestMCPServerConnectionListTools:
    @pytest.mark.asyncio
    async def test_list_tools_returns_all(self) -> None:
        conn = MCPServerConnection("test", _stdio_config())
        mock_session = AsyncMock()
        mock_session.list_tools.return_value = SimpleNamespace(
            tools=["tool1", "tool2"],
            nextCursor=None,
        )
        conn._session = mock_session
        conn._connected = True

        tools = await conn.list_tools()
        assert tools == ["tool1", "tool2"]

    @pytest.mark.asyncio
    async def test_list_tools_paginated(self) -> None:
        conn = MCPServerConnection("test", _stdio_config())
        mock_session = AsyncMock()
        mock_session.list_tools.side_effect = [
            SimpleNamespace(tools=["tool1"], nextCursor="page2"),
            SimpleNamespace(tools=["tool2", "tool3"], nextCursor=None),
        ]
        conn._session = mock_session
        conn._connected = True

        tools = await conn.list_tools()
        assert tools == ["tool1", "tool2", "tool3"]
        assert mock_session.list_tools.await_count == 2

    @pytest.mark.asyncio
    async def test_list_tools_raises_when_not_connected(self) -> None:
        conn = MCPServerConnection("test", _stdio_config())
        with pytest.raises(RuntimeError, match="not connected"):
            await conn.list_tools()


class TestMCPServerConnectionCallTool:
    @pytest.mark.asyncio
    async def test_call_tool_delegates(self) -> None:
        conn = MCPServerConnection("test", _stdio_config())
        mock_session = AsyncMock()
        expected = SimpleNamespace(content=[], isError=False)
        mock_session.call_tool.return_value = expected
        conn._session = mock_session
        conn._connected = True

        result = await conn.call_tool("my_tool", {"a": 1})
        assert result is expected
        mock_session.call_tool.assert_awaited_once_with("my_tool", arguments={"a": 1})

    @pytest.mark.asyncio
    async def test_call_tool_raises_when_not_connected(self) -> None:
        conn = MCPServerConnection("test", _stdio_config())
        with pytest.raises(RuntimeError, match="not connected"):
            await conn.call_tool("x", {})


class TestMCPServerConnectionReconnect:
    @pytest.mark.asyncio
    async def test_reconnect_success(self) -> None:
        config = _stdio_config(reconnect_attempts=2, reconnect_delay_seconds=0)
        conn = MCPServerConnection("test", config)
        conn._connected = True
        conn._session_cm = AsyncMock()
        conn._session_cm.__aexit__ = AsyncMock(return_value=None)
        conn._transport_cm = AsyncMock()
        conn._transport_cm.__aexit__ = AsyncMock(return_value=None)

        with patch.object(conn, "connect", new_callable=AsyncMock):
            result = await conn.reconnect()

        assert result is True

    @pytest.mark.asyncio
    async def test_reconnect_exhausted(self) -> None:
        config = _stdio_config(reconnect_attempts=2, reconnect_delay_seconds=0)
        conn = MCPServerConnection("test", config)
        conn._connected = True
        conn._session_cm = AsyncMock()
        conn._session_cm.__aexit__ = AsyncMock(return_value=None)
        conn._transport_cm = AsyncMock()
        conn._transport_cm.__aexit__ = AsyncMock(return_value=None)

        with patch.object(
            conn, "connect", new_callable=AsyncMock, side_effect=RuntimeError("fail")
        ):
            result = await conn.reconnect()

        assert result is False
