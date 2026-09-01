"""Tests for plugin manifest parsing and validation."""

from pathlib import Path

import pytest

from nahida_bot.core.exceptions import PluginLoadError
from nahida_bot.plugins.manifest import (
    Capabilities,
    DesktopRuntimeFacet,
    DesktopSurfaceDeclaration,
    FilesystemPermission,
    GatewayRuntimeFacet,
    MemoryPermission,
    NetworkPermission,
    NodeRuntimeFacet,
    Permissions,
    PluginManifest,
    PluginContributions,
    PluginPageDeclaration,
    PluginRuntimeFacets,
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
        assert m.load_phase == "post-agent"
        assert m.enabled is True
        assert m.permissions.network.outbound == []
        assert m.permissions.filesystem.read == ["workspace"]
        assert m.runtimes.gateway == GatewayRuntimeFacet(entrypoint="test:TestPlugin")

    def test_full_manifest(self) -> None:
        m = PluginManifest(
            id="com.example.full",
            name="Full Plugin",
            version="2.0.0",
            description="A comprehensive plugin",
            entrypoint="full:FullPlugin",
            nahida_bot_version=">=0.1.0",
            sdk_version=">=0.1.0",
            load_phase="pre-agent",
            enabled=False,
            permissions=Permissions(
                network=NetworkPermission(outbound=["https://api.example.com/*"]),
                filesystem=FilesystemPermission(
                    read=["workspace"], write=["workspace"]
                ),
                memory=MemoryPermission(read=True, write=True),
                system=SystemPermission(env_vars=["MY_PLUGIN_*"], subprocess=True),
            ),
            capabilities=Capabilities(
                tools=[{"name": "web_search", "description": "Search the web"}],
                subscribes_to=["MessageReceived"],
            ),
            config={"api_key": ""},
            config_schema={
                "type": "object",
                "properties": {"api_key": {"type": "string"}},
            },
        )
        assert m.load_phase == "pre-agent"
        assert m.enabled is False
        assert m.permissions.network.outbound == ["https://api.example.com/*"]
        assert m.permissions.system.subprocess is True
        assert m.config_schema["properties"]["api_key"]["type"] == "string"

    def test_default_permissions(self) -> None:
        m = PluginManifest(id="test", name="T", version="0.1.0", entrypoint="t:T")
        assert m.permissions.memory.read is False
        assert m.permissions.memory.write is False
        assert m.permissions.plugin_secrets.read is False
        assert m.permissions.plugin_secrets.write is False
        assert m.permissions.system.subprocess is False
        assert m.permissions.system.env_vars == []

    def test_ui_contributions_share_the_plugin_manifest(self) -> None:
        m = PluginManifest(
            id="com.example.schedule",
            name="Schedule",
            version="0.1.0",
            entrypoint="schedule:SchedulePlugin",
            contributes=PluginContributions(
                desktop_surfaces=[
                    DesktopSurfaceDeclaration(
                        id="today",
                        target="desktop.home",
                        kind="list",
                        priority=20,
                    )
                ],
                pages=[
                    PluginPageDeclaration(
                        id="settings",
                        target="webui.admin",
                        entry="dist/settings.html",
                        title="日程设置",
                    )
                ],
            ),
            runtimes=PluginRuntimeFacets(
                node=NodeRuntimeFacet(
                    entrypoint="dist/worker.js",
                    mode="javascript",
                ),
                desktop=DesktopRuntimeFacet(
                    entrypoint="builtin:com.example.schedule",
                    mode="builtin",
                ),
            ),
        )

        assert m.contributes.desktop_surfaces[0].target == "desktop.home"
        assert m.contributes.pages[0].target == "webui.admin"
        assert m.runtimes.desktop is not None
        assert m.runtimes.desktop.entrypoint == "builtin:com.example.schedule"
        assert m.runtimes.node is not None
        assert m.runtimes.node.mode == "javascript"


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
permissions:
  network:
    outbound:
      - "https://api.example.com/*"
  memory:
    read: true
    write: true
  plugin_secrets:
    read: true
    write: true
""",
        )
        manifest = parse_manifest(path)
        assert manifest.id == "com.example.hello"
        assert manifest.name == "Hello Plugin"
        assert manifest.permissions.network.outbound == ["https://api.example.com/*"]
        assert manifest.permissions.memory.read is True
        assert manifest.permissions.plugin_secrets.read is True
        assert manifest.permissions.plugin_secrets.write is True

    def test_parse_surface_contributions(self, tmp_path: Path) -> None:
        path = _write_manifest(
            tmp_path,
            """
id: com.example.schedule
name: Schedule
version: "1.0.0"
entrypoint: schedule:SchedulePlugin
contributes:
  desktop_surfaces:
    - id: today
      target: pet.drawer
      kind: list
      priority: 15
  pages:
    - id: settings
      target: desktop.main
      entry: dist/settings.html
      title: Schedule settings
runtimes:
  desktop:
    entrypoint: builtin:com.example.schedule
    mode: builtin
""",
        )

        manifest = parse_manifest(path)

        assert manifest.contributes.desktop_surfaces[0].id == "today"
        assert manifest.contributes.desktop_surfaces[0].priority == 15
        assert manifest.contributes.pages[0].entry == "dist/settings.html"
        assert manifest.runtimes.desktop is not None
        assert manifest.runtimes.desktop.mode == "builtin"

    def test_parse_facets_only_manifest_without_gateway_entrypoint(
        self, tmp_path: Path
    ) -> None:
        path = _write_manifest(
            tmp_path,
            """
id: com.example.desktop-only
name: Desktop Only
version: "1.0.0"
runtimes:
  desktop:
    entrypoint: builtin:com.example.desktop-only
    mode: builtin
""",
        )

        manifest = parse_manifest(path)

        assert manifest.entrypoint == ""
        assert manifest.runtimes.gateway is None
        assert manifest.runtimes.desktop is not None

    def test_rejects_manifest_without_any_runtime(self, tmp_path: Path) -> None:
        path = _write_manifest(
            tmp_path,
            """
id: com.example.empty
name: Empty
version: "1.0.0"
""",
        )

        with pytest.raises(PluginLoadError, match="at least one runtime facet"):
            parse_manifest(path)

    def test_rejects_conflicting_gateway_entrypoints(self, tmp_path: Path) -> None:
        path = _write_manifest(
            tmp_path,
            """
id: com.example.conflict
name: Conflict
version: "1.0.0"
entrypoint: legacy:Plugin
runtimes:
  gateway:
    entrypoint: current:Plugin
""",
        )

        with pytest.raises(PluginLoadError, match="entrypoint must match"):
            parse_manifest(path)

    def test_rejects_duplicate_page_ids(self, tmp_path: Path) -> None:
        path = _write_manifest(
            tmp_path,
            """
id: com.example.pages
name: Pages
version: "1.0.0"
runtimes:
  desktop:
    entrypoint: builtin:com.example.pages
contributes:
  pages:
    - id: settings
      target: webui.admin
      entry: dist/webui.html
    - id: settings
      target: desktop.main
      entry: dist/desktop.html
""",
        )

        with pytest.raises(PluginLoadError, match="duplicate page contribution id"):
            parse_manifest(path)

    def test_rejects_invalid_surface_identifiers(self, tmp_path: Path) -> None:
        path = _write_manifest(
            tmp_path,
            """
id: com.example.schedule
name: Schedule
version: "1.0.0"
entrypoint: schedule:SchedulePlugin
contributes:
  desktop_surfaces:
    - id: "../today"
      target: desktop.home
      kind: list
""",
        )

        with pytest.raises(PluginLoadError, match="Invalid manifest"):
            parse_manifest(path)

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
