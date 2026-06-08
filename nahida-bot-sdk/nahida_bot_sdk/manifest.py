"""Plugin manifest model, YAML parsing, and permission declarations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field


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


class PluginManifest(BaseModel):
    """Parsed plugin manifest from plugin.yaml."""

    id: str
    name: str
    version: str
    description: str = ""
    entrypoint: str  # "module_path:ClassName"
    nahida_bot_version: str = ""
    sdk_version: str = ""
    load_phase: Literal["pre-agent", "post-agent"] = "post-agent"
    permissions: Permissions = Field(default_factory=Permissions)
    capabilities: Capabilities = Field(default_factory=Capabilities)
    config: dict[str, Any] = Field(default_factory=dict)
    config_schema: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[PluginDependency] = Field(default_factory=list)


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

    missing = {"id", "name", "version", "entrypoint"} - set(data.keys())
    if missing:
        raise ManifestParseError(
            f"Manifest at {yaml_path} missing required fields: {', '.join(sorted(missing))}"
        )

    return PluginManifest(**data)
