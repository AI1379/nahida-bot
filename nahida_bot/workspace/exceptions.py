"""Workspace-specific exception definitions."""

from nahida_bot.core.exceptions import NahidaBotError


class WorkspaceError(NahidaBotError):
    """Base error for workspace operations."""


class WorkspaceNotFoundError(WorkspaceError):
    """Raised when a requested workspace cannot be found."""


class WorkspaceAlreadyExistsError(WorkspaceError):
    """Raised when attempting to create a duplicated workspace."""


class WorkspacePathError(WorkspaceError):
    """Raised when a sandbox path is invalid or unsafe."""


class WorkspaceValidationError(WorkspaceError):
    """Raised when workspace identifiers or metadata are invalid."""
