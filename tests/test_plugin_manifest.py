"""Tests for plugin manifest parsing and validation."""

from pathlib import Path

import pytest

from nahida_bot.core.exceptions import PluginLoadError
from nahida_bot.plugins.manifest import (
    Capabilities,
    FilesystemPermission,
    MemoryPermission,
    NetworkPermission,
    Permissions,
    PluginManifest,
    SystemPermission,
    parse_manifest,
)


def _write_manifest(tmp: Path, content: str) -> Path:
    yaml_path = tmp / "plugin.yaml"
    yaml_path.write_text(content, encoding="utf-8")
    return yaml_path


class TestPluginManifest:
    """Unit tests for PluginManifest model."""

    def test_minimal_valid_manifest(self) -> None:
        m = PluginManifest(
            id="com.example.test",
            name="Test Plugin",
            version="1.0.0",
            entrypoint="test:TestPlugin",
        )
        assert m.id == "com.example.test"
        assert m.type == "tool"
        assert m.load_phase == "post-agent"
        assert m.permissions.network.outbound == []
        assert m.permissions.filesystem.read == ["workspace"]

    def test_full_manifest(self) -> None:
        m = PluginManifest(
            id="com.example.full",
            name="Full Plugin",
            version="2.0.0",
            description="A comprehensive plugin",
            entrypoint="full:FullPlugin",
            nahida_bot_version=">=0.1.0",
            sdk_version=">=0.1.0",
            type="channel",
            load_phase="pre-agent",
            permissions=Permissions(
                network=NetworkPermission(outbound=["https://api.example.com/*"]),
                filesystem=FilesystemPermission(
                    read=["workspace"], write=["workspace"]
                ),
                memory=MemoryPermission(read=True, write=True),
                system=SystemPermission(env_vars=["MY_PLUGIN_*"], subprocess=True),
            ),
            capabilities=Capabilities(
                channel_protocols=["http_server", "http_client"],
                tools=[{"name": "web_search", "description": "Search the web"}],
                subscribes_to=["MessageReceived"],
            ),
            config={"type": "object", "properties": {"api_key": {"type": "string"}}},
        )
        assert m.type == "channel"
        assert m.load_phase == "pre-agent"
        assert m.permissions.network.outbound == ["https://api.example.com/*"]
        assert m.capabilities.channel_protocols == ["http_server", "http_client"]
        assert m.permissions.system.subprocess is True

    def test_default_permissions(self) -> None:
        m = PluginManifest(id="test", name="T", version="0.1.0", entrypoint="t:T")
        assert m.permissions.memory.read is False
        assert m.permissions.memory.write is False
        assert m.permissions.system.subprocess is False
        assert m.permissions.system.env_vars == []


class TestParseManifest:
    """Tests for YAML manifest parsing."""

    def test_parse_valid_yaml(self, tmp_path: Path) -> None:
        path = _write_manifest(
            tmp_path,
            """
id: com.example.hello
name: Hello Plugin
version: "1.0.0"
description: Says hello
entrypoint: hello:HelloPlugin
type: tool
permissions:
  network:
    outbound:
      - "https://api.example.com/*"
  memory:
    read: true
    write: true
""",
        )
        manifest = parse_manifest(path)
        assert manifest.id == "com.example.hello"
        assert manifest.name == "Hello Plugin"
        assert manifest.permissions.network.outbound == ["https://api.example.com/*"]
        assert manifest.permissions.memory.read is True

    def test_parse_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(PluginLoadError, match="not found"):
            parse_manifest(tmp_path / "nonexistent.yaml")

    def test_parse_invalid_yaml(self, tmp_path: Path) -> None:
        path = _write_manifest(tmp_path, "{{invalid yaml: [}")
        with pytest.raises(PluginLoadError, match="Failed to parse"):
            parse_manifest(path)

    def test_parse_missing_required_fields(self, tmp_path: Path) -> None:
        path = _write_manifest(tmp_path, "name: OnlyName\n")
        with pytest.raises(PluginLoadError, match="missing required fields"):
            parse_manifest(path)

    def test_parse_non_mapping_yaml(self, tmp_path: Path) -> None:
        path = _write_manifest(tmp_path, "- just\n- a\n- list\n")
        with pytest.raises(PluginLoadError, match="must be a YAML mapping"):
            parse_manifest(path)
