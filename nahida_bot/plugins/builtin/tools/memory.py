"""Durable-memory tools for the builtin commands plugin."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import structlog

from nahida_bot.agent.memory.markdown import (
    MEMORY_FILE,
    MEMORY_SUMMARY_FILE,
    MAX_TOOL_READ_CHARS,
    filter_memory_text,
    validate_memory_content,
)
from nahida_bot.plugins.tooling import PluginToolDefinition
from nahida_bot_sdk.api import BotAPI


_logger = structlog.get_logger(__name__)

_KINDS = [
    "fact",
    "preference",
    "task",
    "decision",
    "procedure",
    "warning",
    "summary",
]
_VALID_KINDS = frozenset(_KINDS)
_VALID_AUDIENCES = frozenset({"current", "global"})
_VALID_SENSITIVITIES = frozenset({"public", "private", "secret_like"})
_VALID_SCOPE_TYPES = frozenset({"chat", "person", "account"})

_READ_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Optional text to search for in memory lines.",
        },
        "max_length": {
            "type": "integer",
            "description": "Maximum characters to return. Default 10000.",
        },
    },
    "required": [],
    "additionalProperties": False,
}

_WRITE_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content": {
            "type": "string",
            "description": "Concise memory text to append.",
        },
        "title": {
            "type": "string",
            "description": "Short descriptive title.",
        },
        "kind": {
            "type": "string",
            "enum": _KINDS,
            "description": "Content type. Default fact.",
        },
        "audience": {
            "type": "string",
            "enum": ["current", "global"],
            "description": (
                "Visibility intent. Default current. Use global only for public "
                "bot-wide knowledge that applies across every chat. Summaries "
                "cannot be global."
            ),
        },
        "sensitivity": {
            "type": "string",
            "enum": ["public", "private", "secret_like"],
            "description": (
                "Sensitivity tag (Piece A4). Default public — soft, recallable "
                "according to scope. Use 'private' when the user asks to keep it "
                "between you ('别告诉别人'/'私下'), or 'secret_like' for content "
                "that must NEVER leave this chat (e.g. sensitive personal matters "
                "you promised to keep secret). Raw credentials (passwords/api "
                "keys/tokens) are NEVER stored — do not use this to save them. "
                "private/secret_like notes are stored only in the protected durable "
                "store."
            ),
        },
        "portable": {
            "type": "boolean",
            "description": (
                "Whether a public memory may be recalled outside its primary scope. "
                "Default true. Set false for current-chat-only social context such "
                "as a nickname used only in this group. This is independent from "
                "sensitivity."
            ),
        },
    },
    "required": ["content"],
    "additionalProperties": False,
}

_UPDATE_PROPERTIES: dict[str, Any] = {
    "item_id": {"type": "string"},
    "content": {"type": "string"},
    "title": {"type": "string"},
    "kind": {"type": "string", "enum": _KINDS},
    "audience": {
        "type": "string",
        "enum": ["current", "global"],
        "description": (
            "Visibility intent. Default — keep existing audience. Use 'global' "
            "only to promote a public item to bot-wide visibility. Use 'current' "
            "to demote a global item back to the current chat scope."
        ),
    },
    "sensitivity": {
        "type": "string",
        "enum": ["public", "private", "secret_like"],
    },
    "portable": {
        "type": "boolean",
        "description": (
            "Whether a public item may leave its primary scope during soft-scope "
            "recall. Omit to keep the existing value."
        ),
    },
}

_ARCHIVE_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "item_id": {"type": "string"},
        "reason": {
            "type": "string",
            "description": "Short reason for the archival decision.",
        },
    },
    "required": ["item_id", "reason"],
    "additionalProperties": False,
}


@dataclass(slots=True, frozen=True)
class _MemoryUpdateRequest:
    item_id: str
    content: str
    title: str = ""
    kind: str = ""
    audience: str = ""
    target_scope_type: str = ""
    target_scope_id: str = ""
    sensitivity: str = ""
    portable: bool | None = None

    @classmethod
    def from_arguments(cls, arguments: dict[str, Any]) -> _MemoryUpdateRequest:
        """Build a typed update request from provider keyword arguments."""
        return cls(
            item_id=str(arguments.get("item_id", "")),
            content=str(arguments.get("content", "")),
            title=str(arguments.get("title", "")),
            kind=str(arguments.get("kind", "")),
            audience=str(arguments.get("audience", "")),
            target_scope_type=str(arguments.get("target_scope_type", "")),
            target_scope_id=str(arguments.get("target_scope_id", "")),
            sensitivity=str(arguments.get("sensitivity", "")),
            portable=arguments.get("portable"),
        )


class MemoryTools:
    """Define and execute durable-memory operations."""

    def __init__(self, api: BotAPI) -> None:
        self._api = api

    def definitions(self) -> tuple[PluginToolDefinition, ...]:
        """Return memory tools exposed to the model."""
        return (
            PluginToolDefinition(
                name="memory_read",
                description=(
                    "Search structured durable memory visible to the current chat, "
                    "plus compatible workspace Markdown notes. Use this before "
                    "relying on remembered facts that are not already in context. "
                    "Returned structured entries include item ids for memory_update "
                    "or memory_archive."
                ),
                parameters=_READ_PARAMETERS,
                handler=self.read,
            ),
            PluginToolDefinition(
                name="memory_write",
                description=(
                    "Create one structured durable memory. Use only for stable "
                    "preferences, facts, tasks, decisions, procedures, warnings, "
                    "or explicit requests to remember. Audience defaults to the "
                    "current identity/chat; global is exceptional and must apply "
                    "intentionally across every chat and user."
                ),
                parameters=_WRITE_PARAMETERS,
                handler=self.write,
            ),
            self._update_definition(),
            PluginToolDefinition(
                name="memory_archive",
                description=(
                    "Archive a visible structured memory item only when it is "
                    "obsolete, wrong, duplicated, or explicitly revoked. Read the "
                    "item first and pass its item id."
                ),
                parameters=_ARCHIVE_PARAMETERS,
                handler=self.archive,
            ),
        )

    def _update_definition(self) -> PluginToolDefinition:
        can_reassign = bool(os.environ.get("NAHIDA_MEMORY_REASSIGN"))
        description = (
            "Replace a visible structured memory item when its content or scope is "
            "outdated or incorrect. This creates a replacement with provenance and "
            "archives the old item. Changing audience to 'global' requires the "
            "sensitivity to be 'public'. Do not use merely to rephrase correct memory."
        )
        properties = dict(_UPDATE_PROPERTIES)
        if can_reassign:
            description = (
                "Replace a visible structured memory item when its content or scope "
                "is outdated or incorrect. This creates a replacement with provenance "
                "and archives the old item. Changing audience to 'global' requires "
                "the sensitivity to be 'public'. You may also reassign an item to any "
                "person, account, or chat scope via "
                "target_scope_type+target_scope_id. Do not use merely to rephrase "
                "correct memory."
            )
            properties.update(
                {
                    "target_scope_type": {
                        "type": "string",
                        "enum": ["chat", "person", "account"],
                        "description": (
                            "Reassign to a specific scope type. Use with "
                            "target_scope_id. Overrides audience when both are given."
                        ),
                    },
                    "target_scope_id": {
                        "type": "string",
                        "description": (
                            "Target scope id (e.g. person id, chat key). Required "
                            "when target_scope_type is set."
                        ),
                    },
                }
            )
        return PluginToolDefinition(
            name="memory_update",
            description=description,
            parameters={
                "type": "object",
                "properties": properties,
                "required": ["item_id", "content"],
                "additionalProperties": False,
            },
            handler=self.update,
        )

    async def read(self, query: str = "", max_length: int = 10000) -> str:
        """Read structured and compatible Markdown durable memory."""
        _logger.debug("tool.memory_read", query=query)
        structured = await self._api.memory_search(query, limit=20)
        max_chars = min(max(max_length, 1), MAX_TOOL_READ_CHARS)
        blocks: list[str] = []
        if structured:
            blocks.append(self.format_refs(structured))
        for path in (MEMORY_FILE, MEMORY_SUMMARY_FILE):
            raw = await self.read_workspace_text_or_empty(path)
            filtered = filter_memory_text(raw, query).strip()
            if filtered:
                blocks.append(f"## {path}\n{filtered}")

        if not blocks:
            return "No matching durable memory found."

        result = "\n\n".join(blocks)
        if len(result) > max_chars:
            result = result[:max_chars].rstrip() + "\n... (memory truncated)"
        return result

    async def write(
        self,
        content: str,
        title: str = "",
        kind: str = "fact",
        audience: str = "current",
        sensitivity: str = "public",
        portable: bool = True,
    ) -> str:
        """Create one durable-memory item after policy validation."""
        _logger.debug(
            "tool.memory_write",
            kind=kind,
            audience=audience,
            sensitivity=sensitivity,
            portable=portable,
        )
        error = validate_memory_content(content)
        if error is not None:
            return error
        if kind not in _VALID_KINDS:
            return "Error: invalid memory kind."
        if audience not in _VALID_AUDIENCES:
            return "Error: audience must be current or global."
        if sensitivity not in _VALID_SENSITIVITIES:
            return "Error: sensitivity must be one of: public, private, secret_like."
        if kind == "summary" or sensitivity != "public" or not portable:
            audience = "current"
        try:
            item_id = await self._api.memory_store(
                title,
                content,
                metadata={
                    "source": "memory_tool",
                    "kind": kind,
                    "audience": audience,
                    "sensitivity": sensitivity,
                    "portable": portable,
                },
            )
        except Exception as exc:
            _logger.warning("tool.memory_write_failed", error=str(exc))
            return "Error: failed to store durable memory."
        return f"Memory stored: {item_id or '(id unavailable)'}"

    async def update(self, **arguments: Any) -> str:
        """Replace one visible durable-memory item."""
        request = _MemoryUpdateRequest.from_arguments(arguments)
        validation_error = self._validate_update(request)
        if validation_error:
            return validation_error
        metadata = self._update_metadata(request)
        try:
            replacement_id = await self._api.memory_update(
                request.item_id,
                request.content,
                key=request.title,
                metadata=metadata,
            )
        except Exception as exc:
            _logger.warning(
                "tool.memory_update_failed",
                item_id=request.item_id,
                error=str(exc),
            )
            return "Error: failed to update durable memory."
        if replacement_id is None:
            return (
                "Error: memory item is missing, inaccessible, or could not be updated."
            )
        return f"Memory updated: {request.item_id} -> {replacement_id}"

    @staticmethod
    def _validate_update(request: _MemoryUpdateRequest) -> str | None:
        error = validate_memory_content(request.content)
        if error is not None:
            return error
        if request.kind and request.kind not in _VALID_KINDS:
            return "Error: invalid memory kind."
        if request.audience and request.audience not in _VALID_AUDIENCES:
            return "Error: audience must be current or global."
        if (
            request.target_scope_type
            and request.target_scope_type not in _VALID_SCOPE_TYPES
        ):
            return "Error: target_scope_type must be chat, person, or account."
        if (
            request.target_scope_type or request.target_scope_id
        ) and not os.environ.get("NAHIDA_MEMORY_REASSIGN"):
            return (
                "Error: reassigning memory to an arbitrary scope requires "
                "NAHIDA_MEMORY_REASSIGN=1."
            )
        if request.target_scope_type and not request.target_scope_id:
            return "Error: target_scope_id is required when target_scope_type is set."
        if request.sensitivity and request.sensitivity not in _VALID_SENSITIVITIES:
            return "Error: invalid memory sensitivity."
        return None

    @staticmethod
    def _update_metadata(request: _MemoryUpdateRequest) -> dict[str, Any]:
        metadata: dict[str, Any] = {"update_reason": "bot_memory_tool"}
        if request.kind:
            metadata["kind"] = request.kind
        if request.audience:
            metadata["audience"] = request.audience
        if request.target_scope_type:
            metadata["target_scope_type"] = request.target_scope_type
            metadata["target_scope_id"] = request.target_scope_id
        if request.sensitivity:
            metadata["sensitivity"] = request.sensitivity
        if request.portable is not None:
            metadata["portable"] = request.portable
        return metadata

    async def archive(self, item_id: str, reason: str) -> str:
        """Archive one visible durable-memory item."""
        try:
            archived = await self._api.memory_archive(item_id)
        except Exception as exc:
            _logger.warning(
                "tool.memory_archive_failed", item_id=item_id, error=str(exc)
            )
            return "Error: failed to archive durable memory."
        if not archived:
            return "Error: memory item is missing, inaccessible, or already archived."
        _logger.info("tool.memory_archived", item_id=item_id, reason=reason)
        return f"Memory archived: {item_id}"

    async def read_workspace_text_or_empty(self, path: str) -> str:
        """Read a workspace text file, treating ordinary absence as empty."""
        try:
            return await self._api.workspace_read(path)
        except FileNotFoundError:
            return ""
        except Exception as exc:
            if exc.__class__.__name__ in {
                "WorkspacePathError",
                "WorkspaceNotFoundError",
            }:
                raise
            return ""

    @staticmethod
    def format_refs(results: list[Any]) -> str:
        """Format memory references for model and command output."""
        if not results:
            return "No memory found."
        lines = ["Memory results:"]
        for idx, item in enumerate(results, start=1):
            title = ""
            scope_type = ""
            audience = ""
            sensitivity = ""
            metadata = getattr(item, "metadata", None)
            if isinstance(metadata, dict):
                title_value = metadata.get("title")
                if isinstance(title_value, str) and title_value:
                    title = f"{title_value}: "
                scope_type = str(metadata.get("scope_type", "") or "")
                audience = str(metadata.get("audience", "") or "")
                sensitivity = str(metadata.get("sensitivity", "") or "")
            key = getattr(item, "key", "")
            content = getattr(item, "content", "")
            scope_parts = [p for p in (scope_type, audience, sensitivity) if p]
            scope_label = f" ({', '.join(scope_parts)})" if scope_parts else ""
            lines.append(f"{idx}. [{key}]{scope_label} {title}{str(content)[:500]}")
        return "\n".join(lines)
