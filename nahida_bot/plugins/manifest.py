"""Plugin manifest model and YAML parsing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


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
    system: SystemPermission = Field(default_factory=SystemPermission)


class Capabilities(BaseModel):
    """Capability declarations for a plugin."""

    channel_protocols: list[str] = Field(default_factory=list)
    tools: list[dict[str, str]] = Field(default_factory=list)
    subscribes_to: list[str] = Field(default_factory=list)


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
    type: str = "tool"  # channel | tool | hook | integration | theme
    permissions: Permissions = Field(default_factory=Permissions)
    capabilities: Capabilities = Field(default_factory=Capabilities)
    config: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[PluginDependency] = Field(default_factory=list)


def parse_manifest(yaml_path: Path) -> PluginManifest:
    """Parse a plugin.yaml file into a validated PluginManifest.

    Args:
        yaml_path: Path to the plugin.yaml file.

    Returns:
        Validated PluginManifest instance.

    Raises:
        PluginLoadError: If the file cannot be read or parsed.
    """
    from nahida_bot.core.exceptions import PluginLoadError

    if not yaml_path.is_file():
        raise PluginLoadError(f"Manifest file not found: {yaml_path}")

    try:
        raw = yaml_path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
    except (yaml.YAMLError, OSError) as exc:
        raise PluginLoadError(
            f"Failed to parse manifest at {yaml_path}: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise PluginLoadError(
            f"Manifest at {yaml_path} must be a YAML mapping, got {type(data).__name__}"
        )

    missing = {"id", "name", "version", "entrypoint"} - set(data.keys())
    if missing:
        raise PluginLoadError(
            f"Manifest at {yaml_path} missing required fields: {', '.join(sorted(missing))}"
        )

    return PluginManifest(**data)
