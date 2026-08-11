"""Workspace file tools for the builtin commands plugin."""

from __future__ import annotations

from typing import Any

from nahida_bot.plugins.tooling import PluginToolDefinition
from nahida_bot.workspace.exceptions import WorkspaceError, WorkspacePathError
from nahida_bot_sdk.api import BotAPI


_READ_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": (
                "Path relative to the workspace root. Absolute paths and paths "
                "that escape the workspace are rejected."
            ),
        }
    },
    "required": ["path"],
    "additionalProperties": False,
}

_WRITE_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": (
                "Path relative to the workspace root. Absolute paths and paths "
                "that escape the workspace are rejected."
            ),
        },
        "content": {
            "type": "string",
            "description": "Text content to write.",
        },
    },
    "required": ["path", "content"],
    "additionalProperties": False,
}


class WorkspaceTools:
    """Define and execute text operations scoped to the active workspace."""

    def __init__(self, api: BotAPI) -> None:
        self._api = api

    def definitions(self) -> tuple[PluginToolDefinition, ...]:
        """Return the workspace tools exposed to the model."""
        return (
            PluginToolDefinition(
                name="workspace_read",
                description=(
                    "Read a UTF-8 text file from the current workspace. The path "
                    "must be relative to the workspace root; absolute paths are "
                    "rejected. The workspace root is the same directory exec uses "
                    "as its default working directory, so reuse paths produced by "
                    "exec as relative paths, not absolute ones."
                ),
                parameters=_READ_PARAMETERS,
                handler=self.read,
            ),
            PluginToolDefinition(
                name="workspace_write",
                description=(
                    "Write UTF-8 text content to a file in the current workspace. "
                    "The path must be relative to the workspace root; absolute paths "
                    "are rejected. Same workspace root as workspace_read and exec."
                ),
                parameters=_WRITE_PARAMETERS,
                handler=self.write,
                requires_admin=True,
            ),
        )

    async def read(self, path: str) -> str:
        """Read one workspace-relative UTF-8 file."""
        try:
            return await self._api.workspace_read(path)
        except WorkspacePathError as exc:
            return (
                f"Error: {exc}. workspace_read only accepts paths relative to "
                "the workspace root."
            )
        except WorkspaceError as exc:
            return f"Error reading workspace file: {exc}"

    async def write(self, path: str, content: str) -> str:
        """Write one workspace-relative UTF-8 file."""
        try:
            await self._api.workspace_write(path, content)
        except WorkspacePathError as exc:
            return (
                f"Error: {exc}. workspace_write only accepts paths relative to "
                "the workspace root."
            )
        except WorkspaceError as exc:
            return f"Error writing workspace file: {exc}"
        return f"Written workspace file: {path}"
