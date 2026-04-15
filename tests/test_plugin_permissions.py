"""Tests for the permission checker."""

import pytest

from nahida_bot.core.exceptions import PermissionDenied
from nahida_bot.plugins.manifest import (
    FilesystemPermission,
    MemoryPermission,
    NetworkPermission,
    Permissions,
    PluginManifest,
    SystemPermission,
)
from nahida_bot.plugins.permissions import PermissionChecker


def _checker(**overrides: object) -> PermissionChecker:
    """Create a PermissionChecker with customizable permissions."""
    perms = Permissions(**overrides)  # type: ignore[arg-type]
    manifest = PluginManifest(
        id="test.plugin",
        name="Test",
        version="1.0.0",
        entrypoint="t:T",
        permissions=perms,
    )
    return PermissionChecker(manifest)


class TestNetworkPermissions:
    def test_outbound_allowed(self) -> None:
        checker = _checker(
            network=NetworkPermission(outbound=["https://api.example.com/*"])
        )
        checker.check_network_outbound("https://api.example.com/v1/data")

    def test_outbound_denied_no_patterns(self) -> None:
        checker = _checker()
        with pytest.raises(PermissionDenied, match="no outbound network"):
            checker.check_network_outbound("https://evil.com")

    def test_outbound_denied_wrong_host(self) -> None:
        checker = _checker(
            network=NetworkPermission(outbound=["https://api.example.com/*"])
        )
        with pytest.raises(PermissionDenied, match="no matching"):
            checker.check_network_outbound("https://evil.com/data")

    def test_inbound_allowed(self) -> None:
        checker = _checker(network=NetworkPermission(inbound=True))
        checker.check_network_inbound()

    def test_inbound_denied(self) -> None:
        checker = _checker()
        with pytest.raises(PermissionDenied, match="no inbound"):
            checker.check_network_inbound()


class TestFilesystemPermissions:
    def test_read_workspace_allowed(self) -> None:
        checker = _checker()
        checker.check_filesystem_read("workspace")  # default allows workspace

    def test_read_data_denied(self) -> None:
        checker = _checker()
        with pytest.raises(PermissionDenied, match="cannot read from zone"):
            checker.check_filesystem_read("data")

    def test_write_workspace_allowed(self) -> None:
        checker = _checker(filesystem=FilesystemPermission(write=["workspace"]))
        checker.check_filesystem_write("workspace")

    def test_write_denied_by_default(self) -> None:
        checker = _checker()
        with pytest.raises(PermissionDenied, match="cannot write to zone"):
            checker.check_filesystem_write("workspace")


class TestMemoryPermissions:
    def test_read_allowed(self) -> None:
        checker = _checker(memory=MemoryPermission(read=True))
        checker.check_memory_read()

    def test_read_denied(self) -> None:
        checker = _checker()
        with pytest.raises(PermissionDenied, match="no memory read"):
            checker.check_memory_read()

    def test_write_allowed(self) -> None:
        checker = _checker(memory=MemoryPermission(write=True))
        checker.check_memory_write()

    def test_write_denied(self) -> None:
        checker = _checker()
        with pytest.raises(PermissionDenied, match="no memory write"):
            checker.check_memory_write()


class TestSystemPermissions:
    def test_subprocess_allowed(self) -> None:
        checker = _checker(system=SystemPermission(subprocess=True))
        checker.check_subprocess()

    def test_subprocess_denied(self) -> None:
        checker = _checker()
        with pytest.raises(PermissionDenied, match="no subprocess"):
            checker.check_subprocess()

    def test_env_var_allowed_by_prefix(self) -> None:
        checker = _checker(system=SystemPermission(env_vars=["MY_PLUGIN_*"]))
        checker.check_env_var("MY_PLUGIN_API_KEY")

    def test_env_var_denied_wrong_prefix(self) -> None:
        checker = _checker(system=SystemPermission(env_vars=["MY_PLUGIN_*"]))
        with pytest.raises(PermissionDenied, match="cannot read env var"):
            checker.check_env_var("OTHER_PLUGIN_KEY")

    def test_env_var_denied_no_prefixes(self) -> None:
        checker = _checker()
        with pytest.raises(PermissionDenied, match="no environment variable"):
            checker.check_env_var("ANY_KEY")
