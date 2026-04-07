"""Tests for workspace manager and sandbox."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pytest

from nahida_bot.workspace import (
    WorkspaceAlreadyExistsError,
    WorkspaceManager,
    WorkspaceNotFoundError,
    WorkspacePathError,
    WorkspaceValidationError,
)


class TestWorkspaceManager:
    """Workspace lifecycle and metadata tests."""

    def test_initialize_creates_default_workspace(self, temp_dir: Path) -> None:
        """Initialize should create storage layout and default workspace."""
        # Arrange
        manager = WorkspaceManager(base_dir=temp_dir)

        # Act
        metadata = manager.initialize()

        # Assert
        assert metadata.workspace_id == "default"
        assert metadata.is_default is True
        assert metadata.created_at.tzinfo == UTC
        assert metadata.last_active_at.tzinfo == UTC
        assert (temp_dir / "workspaces" / "default").exists()
        assert (temp_dir / "workspace_index.json").exists()
        assert manager.get_active_workspace().workspace_id == "default"

    def test_create_workspace_with_template_copy(self, temp_dir: Path) -> None:
        """Create workspace should copy all template files into target workspace."""
        # Arrange
        manager = WorkspaceManager(base_dir=temp_dir)
        manager.initialize()

        template_dir = temp_dir / "template"
        nested = template_dir / "nested"
        nested.mkdir(parents=True)
        (template_dir / "README.md").write_text("hello", encoding="utf-8")
        (nested / "config.toml").write_text("debug=true", encoding="utf-8")

        # Act
        metadata = manager.create_workspace("alpha", template_dir=template_dir)

        # Assert
        assert metadata.workspace_id == "alpha"
        assert metadata.is_default is False
        workspace_path = temp_dir / "workspaces" / "alpha"
        assert (workspace_path / "README.md").read_text(encoding="utf-8") == "hello"
        assert (workspace_path / "nested" / "config.toml").read_text(
            encoding="utf-8"
        ) == "debug=true"

    def test_switch_workspace_updates_active_and_last_active(
        self, temp_dir: Path
    ) -> None:
        """Switch workspace should update active file and touch last_active_at."""
        # Arrange
        manager = WorkspaceManager(base_dir=temp_dir)
        manager.initialize()
        manager.create_workspace("alpha")
        before = next(
            item for item in manager.list_workspaces() if item.workspace_id == "alpha"
        )

        # Act
        after = manager.switch_workspace("alpha")

        # Assert
        assert manager.get_active_workspace().workspace_id == "alpha"
        assert after.last_active_at >= before.last_active_at

    def test_create_workspace_raises_on_duplicate_id(self, temp_dir: Path) -> None:
        """Create workspace should reject duplicated workspace IDs."""
        # Arrange
        manager = WorkspaceManager(base_dir=temp_dir)
        manager.initialize()

        # Act / Assert
        with pytest.raises(WorkspaceAlreadyExistsError):
            manager.create_workspace("default")

    def test_switch_workspace_raises_when_missing(self, temp_dir: Path) -> None:
        """Switch workspace should fail if target workspace does not exist."""
        # Arrange
        manager = WorkspaceManager(base_dir=temp_dir)
        manager.initialize()

        # Act / Assert
        with pytest.raises(WorkspaceNotFoundError):
            manager.switch_workspace("missing")

    def test_create_workspace_rejects_unsafe_id(self, temp_dir: Path) -> None:
        """Create workspace should reject identifiers with path-like tokens."""
        # Arrange
        manager = WorkspaceManager(base_dir=temp_dir)
        manager.initialize()

        # Act / Assert
        with pytest.raises(WorkspaceValidationError):
            manager.create_workspace("../escape")

    def test_create_workspace_rolls_back_when_template_missing(
        self, temp_dir: Path
    ) -> None:
        """Create workspace should clean partially created directory on template failure."""
        # Arrange
        manager = WorkspaceManager(base_dir=temp_dir)
        manager.initialize()
        missing_template = temp_dir / "not-found-template"

        # Act / Assert
        with pytest.raises(WorkspaceNotFoundError):
            manager.create_workspace("alpha", template_dir=missing_template)

        assert not (temp_dir / "workspaces" / "alpha").exists()


class TestWorkspaceSandbox:
    """Workspace sandbox path safety tests."""

    def test_sandbox_write_and_read_text(self, temp_dir: Path) -> None:
        """Sandbox should read and write text under workspace root."""
        # Arrange
        manager = WorkspaceManager(base_dir=temp_dir)
        manager.initialize()
        sandbox = manager.get_sandbox("default")

        # Act
        sandbox.write_text("notes/hello.txt", "world")

        # Assert
        assert sandbox.read_text("notes/hello.txt") == "world"

    def test_sandbox_rejects_path_traversal(self, temp_dir: Path) -> None:
        """Sandbox should block relative path traversal outside workspace root."""
        # Arrange
        manager = WorkspaceManager(base_dir=temp_dir)
        manager.initialize()
        sandbox = manager.get_sandbox("default")

        # Act / Assert
        with pytest.raises(WorkspacePathError):
            sandbox.write_text("../escape.txt", "forbidden")

    def test_sandbox_rejects_absolute_paths(self, temp_dir: Path) -> None:
        """Sandbox should block absolute filesystem paths."""
        # Arrange
        manager = WorkspaceManager(base_dir=temp_dir)
        manager.initialize()
        sandbox = manager.get_sandbox("default")

        # Act / Assert
        with pytest.raises(WorkspacePathError):
            sandbox.read_text(str((temp_dir / "outside.txt").resolve()))
