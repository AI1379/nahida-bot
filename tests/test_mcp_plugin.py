"""Tests for MCPPlugin lifecycle and tool registration."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nahida_bot.plugins.mcp.config import MCPServerConfig
from nahida_bot.plugins.mcp.plugin import MCPPlugin
from tests.helpers import RecordingMockBotAPI

# The four management tools registered by MCPPlugin on every load.
_MANAGEMENT_TOOLS = {
    "mcp_add_server",
    "mcp_remove_server",
    "mcp_list_servers",
    "mcp_reload_server",
}


def _make_manifest(config: dict[str, Any] | None = None) -> MagicMock:
    manifest = MagicMock()
    manifest.config = config or {}
    return manifest


def _make_mcp_tool(
    name: str, description: str = "", schema: dict | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        description=description,
        inputSchema=schema or {"type": "object", "properties": {}},
    )


class TestMCPPluginOnLoad:
    @pytest.mark.asyncio
    async def test_no_servers_configured(self) -> None:
        api = RecordingMockBotAPI()
        plugin = MCPPlugin(api, _make_manifest({"servers": {}}))
        await plugin.on_load()
        # Only management tools, no server tools.
        assert set(api.registered_tools.keys()) == _MANAGEMENT_TOOLS

    @pytest.mark.asyncio
    async def test_disabled_server_skipped(self) -> None:
        api = RecordingMockBotAPI()
        plugin = MCPPlugin(
            api,
            _make_manifest(
                {
                    "servers": {
                        "disabled": {
                            "transport": "stdio",
                            "command": "echo",
                            "enabled": False,
                        }
                    }
                }
            ),
        )
        await plugin.on_load()
        # Only management tools — the disabled server contributes none.
        assert set(api.registered_tools.keys()) == _MANAGEMENT_TOOLS

    @pytest.mark.asyncio
    async def test_connect_failure_skips_server(self) -> None:
        api = RecordingMockBotAPI()
        plugin = MCPPlugin(
            api,
            _make_manifest(
                {
                    "servers": {
                        "bad": {
                            "transport": "stdio",
                            "command": "nonexistent",
                        }
                    }
                }
            ),
        )

        with patch("nahida_bot.plugins.mcp.plugin.MCPServerConnection") as MockConn:
            mock_conn = AsyncMock()
            mock_conn.connect.side_effect = RuntimeError("spawn failed")
            MockConn.return_value = mock_conn

            await plugin.on_load()

        assert set(api.registered_tools.keys()) == _MANAGEMENT_TOOLS

    @pytest.mark.asyncio
    async def test_successful_registration(self) -> None:
        api = RecordingMockBotAPI()
        plugin = MCPPlugin(
            api,
            _make_manifest(
                {
                    "servers": {
                        "fs": {
                            "transport": "stdio",
                            "command": "npx",
                            "args": ["server-fs"],
                        }
                    }
                }
            ),
        )

        mcp_tools = [
            _make_mcp_tool("read_file", "Read a file"),
            _make_mcp_tool("write_file", "Write a file"),
        ]

        with patch("nahida_bot.plugins.mcp.plugin.MCPServerConnection") as MockConn:
            mock_conn = AsyncMock()
            mock_conn.server_key = "fs"
            mock_conn.connect = AsyncMock()
            mock_conn.list_tools = AsyncMock(return_value=mcp_tools)
            MockConn.return_value = mock_conn

            await plugin.on_load()

        assert "fs__read_file" in api.registered_tools
        assert "fs__write_file" in api.registered_tools
        assert api.registered_tools["fs__read_file"]["description"] == "Read a file"

    @pytest.mark.asyncio
    async def test_custom_namespace(self) -> None:
        api = RecordingMockBotAPI()
        plugin = MCPPlugin(
            api,
            _make_manifest(
                {
                    "servers": {
                        "my-server": {
                            "transport": "sse",
                            "url": "http://localhost/sse",
                            "namespace": "search",
                        }
                    }
                }
            ),
        )

        with patch("nahida_bot.plugins.mcp.plugin.MCPServerConnection") as MockConn:
            mock_conn = AsyncMock()
            mock_conn.server_key = "my-server"
            mock_conn.connect = AsyncMock()
            mock_conn.list_tools = AsyncMock(return_value=[_make_mcp_tool("query")])
            MockConn.return_value = mock_conn

            await plugin.on_load()

        assert "search__query" in api.registered_tools

    @pytest.mark.asyncio
    async def test_tool_name_conflict_skipped(self) -> None:
        api = RecordingMockBotAPI()
        # register_tool is called for 2 server tools + 4 management tools.
        # Side effects: tool "a" succeeds, tool "b" conflicts, then management tools succeed.
        api.register_tool = MagicMock(
            side_effect=[None, KeyError("conflict"), None, None, None, None]
        )

        plugin = MCPPlugin(
            api,
            _make_manifest(
                {
                    "servers": {
                        "fs": {
                            "transport": "stdio",
                            "command": "echo",
                        }
                    }
                }
            ),
        )

        with patch("nahida_bot.plugins.mcp.plugin.MCPServerConnection") as MockConn:
            mock_conn = AsyncMock()
            mock_conn.server_key = "fs"
            mock_conn.connect = AsyncMock()
            mock_conn.list_tools = AsyncMock(
                return_value=[
                    _make_mcp_tool("a"),
                    _make_mcp_tool("b"),
                ]
            )
            MockConn.return_value = mock_conn

            await plugin.on_load()

    @pytest.mark.asyncio
    async def test_list_tools_failure_skips_registration(self) -> None:
        api = RecordingMockBotAPI()
        plugin = MCPPlugin(
            api,
            _make_manifest(
                {
                    "servers": {
                        "broken": {
                            "transport": "stdio",
                            "command": "echo",
                        }
                    }
                }
            ),
        )

        with patch("nahida_bot.plugins.mcp.plugin.MCPServerConnection") as MockConn:
            mock_conn = AsyncMock()
            mock_conn.server_key = "broken"
            mock_conn.connect = AsyncMock()
            mock_conn.list_tools = AsyncMock(side_effect=RuntimeError("timeout"))
            MockConn.return_value = mock_conn

            await plugin.on_load()

        assert set(api.registered_tools.keys()) == _MANAGEMENT_TOOLS

    @pytest.mark.asyncio
    async def test_multiple_servers(self) -> None:
        api = RecordingMockBotAPI()
        plugin = MCPPlugin(
            api,
            _make_manifest(
                {
                    "servers": {
                        "fs": {
                            "transport": "stdio",
                            "command": "npx",
                        },
                        "web": {
                            "transport": "sse",
                            "url": "http://localhost/sse",
                        },
                    }
                }
            ),
        )

        with patch("nahida_bot.plugins.mcp.plugin.MCPServerConnection") as MockConn:

            def make_conn(server_key: str) -> AsyncMock:
                conn = AsyncMock()
                conn.server_key = server_key
                conn.connect = AsyncMock()
                conn.list_tools = AsyncMock(return_value=[_make_mcp_tool("do_thing")])
                return conn

            MockConn.side_effect = [
                make_conn("fs"),
                make_conn("web"),
            ]

            await plugin.on_load()

        assert "fs__do_thing" in api.registered_tools
        assert "web__do_thing" in api.registered_tools


class TestMCPPluginDynamicServers:
    @pytest.mark.asyncio
    async def test_readding_dynamic_server_replaces_old_tools(self) -> None:
        api = RecordingMockBotAPI()
        plugin = MCPPlugin(
            api,
            _make_manifest(
                {
                    "servers": {},
                    "allowed_dynamic_url_prefixes": ["http://localhost/"],
                }
            ),
        )
        await plugin.on_load()

        first_conn = self._make_conn("dyn")
        second_conn = self._make_conn("dyn")

        with patch(
            "nahida_bot.plugins.mcp.plugin.MCPServerConnection",
            side_effect=[first_conn, second_conn],
        ):
            config = MCPServerConfig(
                transport="sse",
                url="http://localhost/sse",
            )
            first = await plugin.add_server("dyn", config)
            second = await plugin.add_server("dyn", config)

        assert first["status"] == "connected"
        assert second["status"] == "connected"
        first_conn.disconnect.assert_awaited_once()
        assert "dyn__x" in api.registered_tools
        assert "dyn__x-2" not in api.registered_tools

    @pytest.mark.asyncio
    async def test_remove_dynamic_server_unregisters_tools(self) -> None:
        api = RecordingMockBotAPI()
        plugin = MCPPlugin(
            api,
            _make_manifest(
                {
                    "servers": {},
                    "allowed_dynamic_url_prefixes": ["http://localhost/"],
                }
            ),
        )
        await plugin.on_load()

        conn = self._make_conn("dyn")
        with patch(
            "nahida_bot.plugins.mcp.plugin.MCPServerConnection", return_value=conn
        ):
            await plugin.add_server(
                "dyn",
                MCPServerConfig(transport="sse", url="http://localhost/sse"),
            )

        result = await plugin.remove_server("dyn")

        assert result["status"] == "removed"
        conn.disconnect.assert_awaited_once()
        assert "dyn__x" not in api.registered_tools
        assert await api.plugin_data_get("server:dyn") is None

    @pytest.mark.asyncio
    async def test_static_shadow_is_persisted_but_not_connected(self) -> None:
        api = RecordingMockBotAPI()
        plugin = MCPPlugin(
            api,
            _make_manifest(
                {
                    "servers": {
                        "static": {
                            "transport": "sse",
                            "url": "http://static/sse",
                            "enabled": False,
                        }
                    },
                    "allowed_dynamic_url_prefixes": ["http://localhost/"],
                }
            ),
        )
        await plugin.on_load()

        with patch("nahida_bot.plugins.mcp.plugin.MCPServerConnection") as MockConn:
            result = await plugin.add_server(
                "static",
                MCPServerConfig(transport="sse", url="http://localhost/sse"),
            )

        assert result["status"] == "shadowed"
        assert await api.plugin_data_get("server:static") is not None
        MockConn.assert_not_called()

    @pytest.mark.asyncio
    async def test_dynamic_server_policy_denies_unlisted_url(self) -> None:
        api = RecordingMockBotAPI()
        plugin = MCPPlugin(api, _make_manifest({"servers": {}}))
        await plugin.on_load()

        with patch("nahida_bot.plugins.mcp.plugin.MCPServerConnection") as MockConn:
            result = await plugin.add_server(
                "dyn",
                MCPServerConfig(transport="sse", url="http://localhost/sse"),
            )

        assert result["status"] == "policy_denied"
        assert await api.plugin_data_get("server:dyn") is None
        MockConn.assert_not_called()

    def _make_conn(self, server_key: str) -> AsyncMock:
        conn = AsyncMock()
        conn.server_key = server_key
        conn.connect = AsyncMock()
        conn.disconnect = AsyncMock()
        conn.list_tools = AsyncMock(return_value=[_make_mcp_tool("x")])
        return conn


class TestMCPPluginOnUnload:
    @pytest.mark.asyncio
    async def test_disconnects_all_connections(self) -> None:
        api = RecordingMockBotAPI()
        plugin = MCPPlugin(api, _make_manifest({"servers": {}}))

        mock_conn = AsyncMock()
        plugin._connections = {"server1": mock_conn}

        await plugin.on_unload()

        mock_conn.disconnect.assert_awaited_once()
        assert len(plugin._connections) == 0

    @pytest.mark.asyncio
    async def test_disconnect_error_does_not_raise(self) -> None:
        api = RecordingMockBotAPI()
        plugin = MCPPlugin(api, _make_manifest({"servers": {}}))

        bad_conn = AsyncMock()
        bad_conn.server_key = "bad"
        bad_conn.disconnect.side_effect = RuntimeError("socket closed")
        plugin._connections = {"bad": bad_conn}

        await plugin.on_unload()  # Should not raise
        assert len(plugin._connections) == 0
