"""Workspace domain models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(slots=True)
class WorkspaceMetadata:
    """Metadata describing a workspace lifecycle state."""

    workspace_id: str
    created_at: datetime
    last_active_at: datetime
    is_default: bool = False

    @classmethod
    def create(
        cls, workspace_id: str, *, is_default: bool = False
    ) -> WorkspaceMetadata:
        """Build metadata for a newly created workspace."""
        now = datetime.now(UTC)
        return cls(
            workspace_id=workspace_id,
            created_at=now,
            last_active_at=now,
            is_default=is_default,
        )

    def mark_active(self) -> None:
        """Refresh last active timestamp to current UTC time."""
        self.last_active_at = datetime.now(UTC)

    def to_dict(self) -> dict[str, str | bool]:
        """Serialize metadata as JSON-friendly primitives."""
        return {
            "workspace_id": self.workspace_id,
            "created_at": self.created_at.isoformat(),
            "last_active_at": self.last_active_at.isoformat(),
            "is_default": self.is_default,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> WorkspaceMetadata:
        """Build metadata from serialized JSON payload."""
        workspace_id = str(payload["workspace_id"])
        created_at = datetime.fromisoformat(str(payload["created_at"]))
        last_active_at = datetime.fromisoformat(str(payload["last_active_at"]))
        is_default = bool(payload.get("is_default", False))
        return cls(
            workspace_id=workspace_id,
            created_at=created_at,
            last_active_at=last_active_at,
            is_default=is_default,
        )
