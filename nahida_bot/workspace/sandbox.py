"""Workspace filesystem sandbox."""

from __future__ import annotations

from pathlib import Path

from nahida_bot.workspace.exceptions import WorkspacePathError


class WorkspaceSandbox:
    """Guarded file API scoped to a workspace root directory."""

    def __init__(self, root: Path) -> None:
        """Create a sandbox bound to a workspace directory.

        Args:
            root: Root directory that all file operations must stay under.
        """
        self.root = root.resolve(strict=False)

    def read_text(self, relative_path: str, *, encoding: str = "utf-8") -> str:
        """Read a UTF-8 text file under workspace root."""
        target = self.resolve_safe_path(relative_path)
        return target.read_text(encoding=encoding)

    def write_text(
        self, relative_path: str, content: str, *, encoding: str = "utf-8"
    ) -> None:
        """Write UTF-8 text content under workspace root."""
        target = self.resolve_safe_path(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding=encoding)

    def resolve_safe_path(self, relative_path: str) -> Path:
        """Resolve and validate a path is contained in workspace root.

        Raises:
            WorkspacePathError: If the path is absolute or escapes the workspace root.
        """
        candidate = Path(relative_path)
        if candidate.is_absolute():
            raise WorkspacePathError(
                f"Absolute paths are not allowed in workspace sandbox: {relative_path}"
            )

        normalized = (self.root / candidate).resolve(strict=False)
        try:
            normalized.relative_to(self.root)
        except ValueError as exc:
            raise WorkspacePathError(
                f"Path escapes workspace root: {relative_path}"
            ) from exc

        return normalized
