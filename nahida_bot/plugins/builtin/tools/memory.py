"""Durable-memory tools for the builtin commands plugin."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import structlog

from nahida_bot.agent.memory.markdown import (
    MAX_TOOL_READ_CHARS,
    validate_memory_content,
)
from nahida_bot.plugins.tooling import PluginToolDefinition
from nahida_bot_sdk.api import BotAPI


_logger = structlog.get_logger(__name__)

_KINDS = [
    "fact",
    "impression",
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
        "subject_account_key": {
            "type": "string",
            "description": "Optional exact channel:user:id when browsing a particular person's impressions or relationships. Does not expand visibility.",
        },
        "query": {
            "type": "string",
            "description": "Optional text to search for in memory lines.",
        },
        "max_length": {
            "type": "integer",
            "description": "Maximum characters to return. Default 10000.",
        },
        "category": {
            "type": "string",
            "enum": ["bot", "environment", "person", "chat", "relationship", "unknown"],
            "description": "Browse memory about the bot, environment, current person, chat, or relationships. Omit for semantic search.",
        },
    },
    "required": [],
    "additionalProperties": False,
}

_WRITE_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subject": {
            "type": "string",
            "enum": [
                "bot",
                "environment",
                "current_sender",
                "chat",
                "relationship",
                "unknown",
            ],
            "description": "What the memory is about: bot self-knowledge, environment, current_sender's own facts/impressions, shared chat context, or an evidenced relationship. Unknown is never assumed to be the current user. Use kind=impression for qualified interpretations.",
        },
        "subject_account_key": {
            "type": "string",
            "description": "For a third party, their verified channel:user:id from cited conversation evidence; never infer from a display name.",
        },
        "relationship_accounts": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 2,
            "maxItems": 4,
            "description": "Exact account keys of relationship participants. Requires original evidence_turn_ids identifying each participant; impressions must remain qualified, not facts about inner lives.",
        },
        "evidence_turn_ids": {
            "type": "array",
            "items": {"type": "integer"},
            "maxItems": 100,
            "description": "Original turn IDs in the current chat, required for third-party facts. Retain reporting uncertainty in the content.",
        },
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

    async def read(
        self,
        query: str = "",
        max_length: int = 10000,
        category: str = "",
        subject_account_key: str = "",
    ) -> str:
        """Read attributed durable memory through the shared retrieval policy."""
        _logger.debug("tool.memory_read", query=query)
        if subject_account_key and not category:
            category = "person"
        structured = (
            await self._api.memory_browse(
                category, query, limit=20, account_key=subject_account_key
            )
            if category
            else await self._api.memory_search(query, limit=20)
        )
        max_chars = min(max(max_length, 1), MAX_TOOL_READ_CHARS)
        from nahida_bot.agent.memory.rendering import format_memory_items, item_from_ref

        return format_memory_items(
            [item_from_ref(item) for item in structured], max_chars=max_chars
        )

    async def write(
        self,
        content: str,
        title: str = "",
        kind: str = "fact",
        audience: str = "current",
        sensitivity: str = "public",
        portable: bool = True,
        subject: str = "unknown",
        subject_account_key: str = "",
        evidence_turn_ids: list[int] | None = None,
        relationship_accounts: list[str] | None = None,
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
                    "subject": subject,
                    "subject_account_key": subject_account_key,
                    "evidence_turn_ids": evidence_turn_ids,
                    "relationship_accounts": relationship_accounts,
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

    @staticmethod
    def format_refs(results: list[Any]) -> str:
        """Preserve the same attribution as automatic recall."""
        from nahida_bot.agent.memory.rendering import format_memory_items, item_from_ref

        return format_memory_items([item_from_ref(item) for item in results])
