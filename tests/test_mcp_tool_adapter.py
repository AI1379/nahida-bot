"""Tests for MCP tool adapter: name sanitization, result serialization, handler."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from nahida_bot.plugins.mcp.tool_adapter import (
    build_safe_tool_name,
    create_tool_handler,
    mcp_tool_to_entry,
    serialize_mcp_result,
)


class TestBuildSafeToolName:
    def test_basic_naming(self) -> None:
        assert build_safe_tool_name("fs", "read_file") == "fs__read_file"

    def test_special_chars_sanitized(self) -> None:
        name = build_safe_tool_name("my.server", "some tool!")
        assert name == "my-server__some-tool-"

    def test_long_name_truncated(self) -> None:
        long_server = "a" * 50
        long_tool = "b" * 100
        name = build_safe_tool_name(long_server, long_tool)
        assert len(name) <= 64

    def test_empty_server_gets_default(self) -> None:
        name = build_safe_tool_name("", "read")
        assert name.startswith("server__")

    def test_empty_tool_gets_default(self) -> None:
        name = build_safe_tool_name("fs", "")
        assert name.endswith("__tool")

    def test_collision_suffix(self) -> None:
        reserved = {"fs__read_file"}
        name = build_safe_tool_name("fs", "read_file", reserved_names=reserved)
        assert name == "fs__read_file-2"

    def test_multiple_collisions(self) -> None:
        reserved = {"fs__read_file", "fs__read_file-2", "fs__read_file-3"}
        name = build_safe_tool_name("fs", "read_file", reserved_names=reserved)
        assert name == "fs__read_file-4"

    def test_no_collision_no_suffix(self) -> None:
        reserved = {"other__tool"}
        name = build_safe_tool_name("fs", "read_file", reserved_names=reserved)
        assert name == "fs__read_file"


class TestSerializeMcpResult:
    def test_text_content(self) -> None:
        item = SimpleNamespace(type="text", text="hello world")
        result = SimpleNamespace(content=[item], isError=False)
        assert serialize_mcp_result(result) == "hello world"

    def test_multiple_text_parts(self) -> None:
        a = SimpleNamespace(type="text", text="line1")
        b = SimpleNamespace(type="text", text="line2")
        result = SimpleNamespace(content=[a, b], isError=False)
        assert serialize_mcp_result(result) == "line1\nline2"

    def test_error_result(self) -> None:
        item = SimpleNamespace(type="text", text="something broke")
        result = SimpleNamespace(content=[item], isError=True)
        assert serialize_mcp_result(result) == "[MCP Error] something broke"

    def test_image_content(self) -> None:
        item = SimpleNamespace(type="image", data="abc123", mimeType="image/png")
        result = SimpleNamespace(content=[item], isError=False)
        text = serialize_mcp_result(result)
        assert "[Image: image/png" in text
        assert "6 chars base64" in text

    def test_resource_with_text(self) -> None:
        resource = SimpleNamespace(text="file contents here")
        item = SimpleNamespace(type="resource", resource=resource)
        result = SimpleNamespace(content=[item], isError=False)
        assert serialize_mcp_result(result) == "file contents here"

    def test_resource_without_text(self) -> None:
        resource = SimpleNamespace(uri="file:///tmp/data")
        item = SimpleNamespace(type="resource", resource=resource)
        result = SimpleNamespace(content=[item], isError=False)
        text = serialize_mcp_result(result)
        assert "[Resource:" in text

    def test_structured_content_fallback(self) -> None:
        result = SimpleNamespace(
            content=[], isError=False, structuredContent={"key": "value"}
        )
        text = serialize_mcp_result(result)
        assert "key" in text and "value" in text

    def test_empty_content_status_summary(self) -> None:
        result = SimpleNamespace(content=[], isError=False)
        text = serialize_mcp_result(result)
        assert text == "[MCP result: ok]"

    def test_empty_error_status_summary(self) -> None:
        result = SimpleNamespace(content=[], isError=True)
        text = serialize_mcp_result(result)
        assert text == "[MCP Error] [MCP result: error]"


class TestCreateToolHandler:
    @pytest.mark.asyncio
    async def test_successful_call(self) -> None:
        conn = AsyncMock()
        conn.server_key = "test"
        conn.call_tool.return_value = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="result text")],
            isError=False,
        )

        handler = create_tool_handler(conn, "my_tool", timeout=10.0)
        output = await handler(arg1="value")

        assert output == "result text"
        conn.call_tool.assert_awaited_once_with("my_tool", {"arg1": "value"})

    @pytest.mark.asyncio
    async def test_timeout_error(self) -> None:
        conn = AsyncMock()
        conn.server_key = "test"

        async def slow_call(*args: Any, **kwargs: Any) -> None:
            await asyncio.sleep(10)

        conn.call_tool.side_effect = slow_call

        handler = create_tool_handler(conn, "slow_tool", timeout=0.01)
        output = await handler()

        assert "[MCP Error]" in output
        assert "timed out" in output

    @pytest.mark.asyncio
    async def test_reconnect_on_failure(self) -> None:
        conn = AsyncMock()
        conn.server_key = "test"
        conn.reconnect.return_value = True
        conn.call_tool.side_effect = [
            RuntimeError("connection lost"),
            SimpleNamespace(
                content=[SimpleNamespace(type="text", text="recovered")],
                isError=False,
            ),
        ]

        handler = create_tool_handler(conn, "flaky_tool", timeout=5.0)
        output = await handler(x=1)

        assert output == "recovered"
        assert conn.call_tool.await_count == 2
        conn.reconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reconnect_fails(self) -> None:
        conn = AsyncMock()
        conn.server_key = "test"
        conn.reconnect.return_value = False
        conn.call_tool.side_effect = RuntimeError("boom")

        handler = create_tool_handler(conn, "broken_tool", timeout=5.0)
        output = await handler()

        assert "[MCP Error]" in output
        assert "reconnect failed" in output


class TestMcpToolToEntry:
    def test_converts_tool(self) -> None:
        conn = AsyncMock()
        conn.server_key = "test"

        mcp_tool = SimpleNamespace(
            name="read_file",
            description="Read a file",
            inputSchema={"type": "object", "properties": {"path": {"type": "string"}}},
        )

        name, desc, params, handler = mcp_tool_to_entry(
            conn, "fs", mcp_tool, timeout=30.0
        )

        assert name == "fs__read_file"
        assert desc == "Read a file"
        assert params["type"] == "object"
        assert callable(handler)

    def test_missing_schema_gets_default(self) -> None:
        conn = AsyncMock()
        mcp_tool = SimpleNamespace(name="ping", description="", inputSchema=None)

        name, desc, params, _handler = mcp_tool_to_entry(
            conn, "svc", mcp_tool, timeout=10.0
        )

        assert name == "svc__ping"
        assert desc == ""
        assert params == {"type": "object", "properties": {}}

    def test_reserved_name_collision(self) -> None:
        conn = AsyncMock()
        mcp_tool = SimpleNamespace(name="read", description="Read", inputSchema=None)

        name, _, _, _ = mcp_tool_to_entry(
            conn, "fs", mcp_tool, timeout=10.0, reserved_names={"fs__read"}
        )

        assert name == "fs__read-2"
