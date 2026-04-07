"""Workspace lifecycle and metadata manager."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from nahida_bot.workspace.exceptions import (
    WorkspaceAlreadyExistsError,
    WorkspaceNotFoundError,
    WorkspaceValidationError,
)
from nahida_bot.workspace.models import WorkspaceMetadata
from nahida_bot.workspace.sandbox import WorkspaceSandbox


class WorkspaceManager:
    """Manage workspace creation, default workspace, and active selection."""

    def __init__(
        self, base_dir: Path, *, default_workspace_id: str = "default"
    ) -> None:
        """Initialize workspace manager.

        Args:
            base_dir: Directory that holds workspace folders and metadata index.
            default_workspace_id: Workspace ID used when bootstrapping a default workspace.
        """
        self.base_dir = base_dir.resolve(strict=False)
        self.workspaces_dir = self.base_dir / "workspaces"
        self.meta_file = self.base_dir / "workspace_index.json"
        self.active_file = self.base_dir / "active_workspace"
        self.default_workspace_id = self._validate_workspace_id(default_workspace_id)

    def initialize(self) -> WorkspaceMetadata:
        """Initialize storage and ensure the default workspace exists."""
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)

        records = self._load_records()
        if self.default_workspace_id not in records:
            records[self.default_workspace_id] = WorkspaceMetadata.create(
                self.default_workspace_id,
                is_default=True,
            )
            self._persist_records(records)
            (self.workspaces_dir / self.default_workspace_id).mkdir(
                parents=True,
                exist_ok=True,
            )

        default_metadata = records[self.default_workspace_id]
        default_metadata.is_default = True
        default_metadata.mark_active()
        records[self.default_workspace_id] = default_metadata
        self._persist_records(records)

        if not self.active_file.exists():
            self.active_file.write_text(self.default_workspace_id, encoding="utf-8")

        return default_metadata

    def create_workspace(
        self,
        workspace_id: str,
        *,
        template_dir: Path | None = None,
        make_active: bool = False,
    ) -> WorkspaceMetadata:
        """Create a workspace and optionally copy a template directory."""
        workspace_id = self._validate_workspace_id(workspace_id)
        records = self._load_records()
        if workspace_id in records:
            raise WorkspaceAlreadyExistsError(
                f"Workspace already exists: {workspace_id}"
            )

        workspace_path = self.workspace_path(workspace_id)
        workspace_path.mkdir(parents=True, exist_ok=False)

        try:
            if template_dir is not None:
                self._copy_template(
                    template_dir=template_dir, target_dir=workspace_path
                )
        except Exception:
            shutil.rmtree(workspace_path, ignore_errors=True)
            raise

        metadata = WorkspaceMetadata.create(workspace_id, is_default=False)
        records[workspace_id] = metadata
        self._persist_records(records)

        if make_active:
            return self.switch_workspace(workspace_id)

        return metadata

    def switch_workspace(self, workspace_id: str) -> WorkspaceMetadata:
        """Switch active workspace and refresh last active timestamp."""
        workspace_id = self._validate_workspace_id(workspace_id)
        records = self._load_records()
        if workspace_id not in records:
            raise WorkspaceNotFoundError(f"Workspace not found: {workspace_id}")

        metadata = records[workspace_id]
        metadata.mark_active()
        records[workspace_id] = metadata
        self._persist_records(records)
        self.active_file.write_text(workspace_id, encoding="utf-8")
        return metadata

    def list_workspaces(self) -> list[WorkspaceMetadata]:
        """Return all known workspaces sorted by ID."""
        records = self._load_records()
        return [records[key] for key in sorted(records)]

    def get_active_workspace(self) -> WorkspaceMetadata:
        """Return metadata for current active workspace."""
        if not self.active_file.exists():
            self.initialize()

        workspace_id = self.active_file.read_text(encoding="utf-8").strip()
        records = self._load_records()
        if workspace_id not in records:
            raise WorkspaceNotFoundError(
                f"Active workspace not found in index: {workspace_id}"
            )
        return records[workspace_id]

    def get_sandbox(self, workspace_id: str | None = None) -> WorkspaceSandbox:
        """Build a sandbox for given workspace or current active workspace."""
        selected_workspace = workspace_id
        if selected_workspace is None:
            selected_workspace = self.get_active_workspace().workspace_id

        records = self._load_records()
        if selected_workspace not in records:
            raise WorkspaceNotFoundError(f"Workspace not found: {selected_workspace}")
        return WorkspaceSandbox(self.workspace_path(selected_workspace))

    def workspace_path(self, workspace_id: str) -> Path:
        """Return root path for a workspace ID."""
        safe_id = self._validate_workspace_id(workspace_id)
        return self.workspaces_dir / safe_id

    def _validate_workspace_id(self, workspace_id: str) -> str:
        value = workspace_id.strip()
        if not value:
            raise WorkspaceValidationError("Workspace ID cannot be empty")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise WorkspaceValidationError(
                "Workspace ID must only contain letters, digits, '_' or '-'"
            )
        return value

    def _load_records(self) -> dict[str, WorkspaceMetadata]:
        if not self.meta_file.exists():
            return {}

        payload = json.loads(self.meta_file.read_text(encoding="utf-8"))
        items = payload.get("workspaces", {})
        return {
            key: WorkspaceMetadata.from_dict(value)
            for key, value in items.items()
            if isinstance(value, dict)
        }

    def _persist_records(self, records: dict[str, WorkspaceMetadata]) -> None:
        serializable = {
            workspace_id: metadata.to_dict()
            for workspace_id, metadata in records.items()
        }
        payload = {"workspaces": serializable}
        self.meta_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _copy_template(self, *, template_dir: Path, target_dir: Path) -> None:
        template = template_dir.resolve(strict=False)
        if not template.exists() or not template.is_dir():
            raise WorkspaceNotFoundError(
                f"Workspace template directory not found: {template_dir}"
            )

        for source_path in template.rglob("*"):
            relative = source_path.relative_to(template)
            destination = target_dir / relative
            if source_path.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue

            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination)
