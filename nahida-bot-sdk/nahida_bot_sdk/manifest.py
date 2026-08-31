"""Plugin manifest model, YAML parsing, and permission declarations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

from nahida_bot_sdk.desktop import DesktopSurfaceKind, DesktopSurfaceTarget


class ManifestParseError(Exception):
    """Raised when a plugin manifest cannot be read or parsed."""


class NetworkPermission(BaseModel):
    """Network access permissions."""

    outbound: list[str] = Field(default_factory=list)
    inbound: bool = False


class FilesystemPermission(BaseModel):
    """Filesystem access permissions."""

    read: list[str] = Field(default_factory=lambda: ["workspace"])
    write: list[str] = Field(default_factory=list)


class MemoryPermission(BaseModel):
    """Memory store access permissions."""

    read: bool = False
    write: bool = False


class PluginDataPermission(BaseModel):
    """Plugin data store access permissions."""

    read: bool = False
    write: bool = False


class SystemPermission(BaseModel):
    """System-level access permissions."""

    env_vars: list[str] = Field(default_factory=list)
    subprocess: bool = False
    signal_handlers: bool = False


class Permissions(BaseModel):
    """Aggregate permission declarations for a plugin."""

    network: NetworkPermission = Field(default_factory=NetworkPermission)
    filesystem: FilesystemPermission = Field(default_factory=FilesystemPermission)
    memory: MemoryPermission = Field(default_factory=MemoryPermission)
    plugin_data: PluginDataPermission = Field(default_factory=PluginDataPermission)
    system: SystemPermission = Field(default_factory=SystemPermission)
    llm_access: bool = False


class Capabilities(BaseModel):
    """Capability declarations for a plugin."""

    tools: list[dict[str, str]] = Field(default_factory=list)
    subscribes_to: list[str] = Field(default_factory=list)
    emits: list[str] = Field(default_factory=list)


class PluginDependency(BaseModel):
    """A plugin dependency declaration."""

    id: str
    version: str = ""


class DesktopSurfaceDeclaration(BaseModel):
    """A host-rendered Desktop surface owned by the plugin."""

    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    target: DesktopSurfaceTarget
    kind: DesktopSurfaceKind
    priority: int = Field(default=0, ge=-100, le=100)


class PluginPageDeclaration(BaseModel):
    """A sandboxed page contribution for a host-specific management surface."""

    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    target: Literal["webui.admin", "desktop.main", "desktop.popup"]
    entry: str = Field(min_length=1, max_length=256)
    title: str = ""


class PluginContributions(BaseModel):
    """Declarative UI contributions shipped in one logical plugin package."""

    desktop_surfaces: list[DesktopSurfaceDeclaration] = Field(
        default_factory=list, max_length=64
    )
    pages: list[PluginPageDeclaration] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def require_unique_ids(self) -> "PluginContributions":
        for label, items in (
            ("desktop surface", self.desktop_surfaces),
            ("page", self.pages),
        ):
            ids = [item.id for item in items]
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {label} contribution id")
        return self


class GatewayRuntimeFacet(BaseModel):
    """Gateway implementation executed by the Python plugin host."""

    entrypoint: str = Field(min_length=1, max_length=256)
    mode: Literal["python"] = "python"


class NodeRuntimeFacet(BaseModel):
    """Worker implementation shipped for a Node runtime."""

    entrypoint: str = Field(min_length=1, max_length=256)
    mode: Literal["python", "javascript", "wasm", "sidecar"] = "python"


class DesktopRuntimeFacet(BaseModel):
    """Desktop implementation shipped by the same logical plugin package."""

    entrypoint: str = Field(min_length=1, max_length=256)
    mode: Literal["builtin", "javascript", "wasm", "sidecar"] = "builtin"


class PluginRuntimeFacets(BaseModel):
    """Technology-specific execution facets governed by one plugin manifest."""

    gateway: GatewayRuntimeFacet | None = None
    node: NodeRuntimeFacet | None = None
    desktop: DesktopRuntimeFacet | None = None


class PluginManifest(BaseModel):
    """Parsed plugin manifest from plugin.yaml."""

    id: str
    name: str
    version: str
    description: str = ""
    # Legacy shorthand for runtimes.gateway.entrypoint. It remains populated
    # for backward compatibility when a gateway facet is declared explicitly.
    entrypoint: str = ""
    nahida_bot_version: str = ""
    sdk_version: str = ""
    load_phase: Literal["pre-agent", "post-agent"] = "post-agent"
    enabled: bool = True
    permissions: Permissions = Field(default_factory=Permissions)
    capabilities: Capabilities = Field(default_factory=Capabilities)
    contributes: PluginContributions = Field(default_factory=PluginContributions)
    runtimes: PluginRuntimeFacets = Field(default_factory=PluginRuntimeFacets)
    config: dict[str, Any] = Field(default_factory=dict)
    config_schema: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[PluginDependency] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_runtime_facets(self) -> "PluginManifest":
        """Normalize the legacy Python entrypoint into the runtime map."""
        gateway = self.runtimes.gateway
        if self.entrypoint and gateway is None:
            self.runtimes.gateway = GatewayRuntimeFacet(entrypoint=self.entrypoint)
        elif gateway is not None and not self.entrypoint:
            self.entrypoint = gateway.entrypoint
        elif gateway is not None and gateway.entrypoint != self.entrypoint:
            raise ValueError(
                "entrypoint must match runtimes.gateway.entrypoint when both are set"
            )

        if not any((self.runtimes.gateway, self.runtimes.node, self.runtimes.desktop)):
            raise ValueError("plugin must declare at least one runtime facet")
        return self


def parse_manifest(yaml_path: Path) -> PluginManifest:
    """Parse a plugin.yaml file into a validated PluginManifest.

    Args:
        yaml_path: Path to the plugin.yaml file.

    Returns:
        Validated PluginManifest instance.

    Raises:
        ManifestParseError: If the file cannot be read or parsed.
    """
    if not yaml_path.is_file():
        raise ManifestParseError(f"Manifest file not found: {yaml_path}")

    try:
        raw = yaml_path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
    except (yaml.YAMLError, OSError) as exc:
        raise ManifestParseError(
            f"Failed to parse manifest at {yaml_path}: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ManifestParseError(
            f"Manifest at {yaml_path} must be a YAML mapping, got {type(data).__name__}"
        )

    missing = {"id", "name", "version"} - set(data.keys())
    if missing:
        raise ManifestParseError(
            f"Manifest at {yaml_path} missing required fields: {', '.join(sorted(missing))}"
        )

    try:
        return PluginManifest(**data)
    except ValidationError as exc:
        raise ManifestParseError(f"Invalid manifest at {yaml_path}: {exc}") from exc
