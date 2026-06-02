"""Re-export shim — canonical types live in nahida_bot_sdk.

The parse_manifest function wraps the SDK version to translate
ManifestParseError into PluginLoadError for backward compatibility.
"""

from nahida_bot.core.exceptions import PluginLoadError
from nahida_bot_sdk.manifest import (  # noqa: F401
    Capabilities,
    FilesystemPermission,
    ManifestParseError,
    MemoryPermission,
    NetworkPermission,
    Permissions,
    PluginDependency,
    PluginManifest,
    SystemPermission,
)
from nahida_bot_sdk.manifest import (
    parse_manifest as _sdk_parse_manifest,
)
from pathlib import Path


def parse_manifest(yaml_path: Path) -> PluginManifest:
    """Parse a plugin.yaml file into a validated PluginManifest.

    Wraps the SDK implementation, translating ManifestParseError
    into PluginLoadError for backward compatibility.
    """
    try:
        return _sdk_parse_manifest(yaml_path)
    except ManifestParseError as exc:
        raise PluginLoadError(str(exc)) from exc
