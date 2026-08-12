"""Cross-session history and chat lookup tools."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from nahida_bot.plugins.tooling import PluginToolDefinition
from nahida_bot_sdk.api import BotAPI


_logger = structlog.get_logger(__name__)

_MAX_TOOL_OUTPUT = 200_000
_MAX_TURN_CHARS = 8000
_BASE64_DATA_URL_RE = re.compile(r'data:[^;"]+;base64,[A-Za-z0-9+/=]+')
_LONG_BASE64_RE = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")

_READ_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "mode": {
            "type": "string",
            "enum": ["recent", "time_range", "around_message", "search"],
            "description": "How to select the history slice.",
        },
        "chat_address": {
            "type": "string",
            "description": "Optional typed chat target, e.g. milky:group:20001.",
        },
        "session_id": {
            "type": "string",
            "description": (
                "Optional exact derived session id instead of a whole chat."
            ),
        },
        "query": {"type": "string", "description": "Required for search mode."},
        "message_id": {
            "type": "string",
            "description": "Required for around_message mode.",
        },
        "since": {
            "type": "string",
            "description": "Optional ISO-8601 inclusive start time.",
        },
        "until": {
            "type": "string",
            "description": "Optional ISO-8601 inclusive end time.",
        },
        "before_turn_id": {
            "type": "integer",
            "description": "Pagination cursor: return turns older than this turn id.",
        },
        "before": {
            "type": "integer",
            "description": "Neighbor turns before an anchor/search hit; default 5.",
        },
        "after": {
            "type": "integer",
            "description": "Neighbor turns after an anchor/search hit; default 5.",
        },
        "limit": {
            "type": "integer",
            "description": "Maximum recent turns or search hits; default 50, max 100.",
        },
    },
    "required": ["mode"],
    "additionalProperties": False,
}

_SEARCH_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Text to search for across all conversation history.",
        },
        "chat_address": {
            "type": "string",
            "description": (
                "Optional: narrow to one chat, e.g. 'milky:group:20001' "
                "(prefix match). Use find_chat to resolve a name to this."
            ),
        },
        "role": {
            "type": "string",
            "enum": ["user", "assistant", "system"],
            "description": "Optional: only return turns of this role.",
        },
        "limit": {
            "type": "integer",
            "description": "Max results. Default 20, capped at 50.",
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}

_FIND_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Chat/group name or substring to search.",
        },
        "platform": {
            "type": "string",
            "description": "Optional: limit to a platform (milky/telegram/onebot).",
        },
    },
    "required": ["name"],
    "additionalProperties": False,
}


@dataclass(slots=True, frozen=True)
class _HistorySelection:
    mode: str = "recent"
    chat_address: str = ""
    session_id: str = ""
    query: str = ""
    message_id: str = ""
    since: str = ""
    until: str = ""
    before_turn_id: int | None = None
    before: int = 5
    after: int = 5
    limit: int = 50

    @classmethod
    def from_arguments(cls, arguments: dict[str, Any]) -> _HistorySelection:
        """Normalize provider arguments while retaining legacy defaults."""
        mode = str(arguments.get("mode", "recent")).strip().lower() or "recent"
        before_turn_id = arguments.get("before_turn_id")
        return cls(
            mode=mode,
            chat_address=str(arguments.get("chat_address", "")).strip(),
            session_id=str(arguments.get("session_id", "")).strip(),
            query=str(arguments.get("query", "")).strip(),
            message_id=str(arguments.get("message_id", "")).strip(),
            since=str(arguments.get("since", "")),
            until=str(arguments.get("until", "")),
            before_turn_id=(
                int(before_turn_id) if before_turn_id is not None else None
            ),
            before=max(min(int(arguments.get("before", 5)), 20), 0),
            after=max(min(int(arguments.get("after", 5)), 20), 0),
            limit=max(min(int(arguments.get("limit") or 50), 100), 1),
        )

    def validation_error(self) -> str | None:
        """Return a user-facing selection error, if any."""
        if self.mode not in {"recent", "time_range", "around_message", "search"}:
            return f"Unsupported history mode: {self.mode}"
        if self.mode == "around_message" and not self.message_id:
            return "around_message mode requires message_id."
        if self.mode == "search" and not self.query:
            return "search mode requires query."
        return None

    @property
    def target_label(self) -> str:
        return self.chat_address or self.session_id or "current chat"


class HistoryTools:
    """Define and execute cross-session history operations."""

    def __init__(self, api: BotAPI) -> None:
        self._api = api

    def definitions(self) -> tuple[PluginToolDefinition, ...]:
        """Return history tools exposed to the model."""
        return (
            PluginToolDefinition(
                name="read_chat_history",
                description=(
                    "Read a chronological slice of raw chat history when nearby "
                    "automatic context is not enough. Supports recent messages, time "
                    "ranges, context around one platform message_id, and text search "
                    "with neighboring turns. Omit chat_address/session_id to read the "
                    "current chat. To continue a discussion from another group/private "
                    "chat, resolve the chat with find_chat and pass its typed "
                    "chat_address. Cross-chat results are private recall: preserve "
                    "provenance and do not reveal private messages to a different "
                    "audience without authorization. Use before_turn_id to page "
                    "backward through long history."
                ),
                parameters=_READ_PARAMETERS,
                handler=self.read,
                requires_admin=True,
            ),
            PluginToolDefinition(
                name="search_chat_history",
                description=(
                    "Search ALL past conversations across every chat — both what users "
                    "said and what you said — to recall something from another "
                    "session/chat. Use sparingly, only when the user wants to remember "
                    "something from elsewhere (e.g. 'do you remember when we talked "
                    "about X', or continuing a thread from another group/private chat). "
                    "Results may include private 1:1 content from other people — treat "
                    "it as reference for your own recall, and do not volunteer others' "
                    "private messages in a group. Narrow with chat_address (use "
                    "find_chat to resolve a name) or role when you can. This is a recall "
                    "aid, not a way to surveil."
                ),
                parameters=_SEARCH_PARAMETERS,
                handler=self.search,
                requires_admin=True,
            ),
            PluginToolDefinition(
                name="find_chat",
                description=(
                    "Fuzzy-search a chat by group/chat name to resolve its address "
                    "(e.g. '原神' -> milky:group:20001). Use before "
                    "search_chat_history (to narrow) or the message tool (to target) "
                    "when the user refers to a chat by name rather than id. Only knows "
                    "chats the bot has seen."
                ),
                parameters=_FIND_PARAMETERS,
                handler=self.find_chat,
                requires_admin=True,
            ),
        )

    async def read(self, **arguments: Any) -> str:
        """Read and format one selected history slice."""
        selection = _HistorySelection.from_arguments(arguments)
        if error := selection.validation_error():
            return error
        try:
            since = self.parse_datetime(selection.since)
            until = self.parse_datetime(selection.until)
        except ValueError as exc:
            return f"Invalid history time: {exc}"
        if selection.mode == "time_range" and since is None and until is None:
            return "time_range mode requires since and/or until."

        rows = await self._api.read_chat_history(
            mode=selection.mode,
            chat_address=selection.chat_address,
            session_id=selection.session_id,
            query=selection.query,
            message_id=selection.message_id,
            since=since,
            until=until,
            before_turn_id=selection.before_turn_id,
            before=selection.before,
            after=selection.after,
            limit=selection.limit,
        )
        if not rows:
            return "No chat history found for that selection."
        return self._format_history_rows(rows, selection)

    def _format_history_rows(
        self,
        rows: list[dict[str, Any]],
        selection: _HistorySelection,
    ) -> str:
        lines = [
            (
                f"Chat history ({selection.mode}, {selection.target_label}), "
                f"{len(rows)} turns, chronological:"
            ),
            f"Older-page cursor: before_turn_id={rows[0].get('turn_id', '')}",
        ]
        total_chars = sum(len(line) for line in lines)
        for row in rows:
            block = self._format_history_row(row)
            if total_chars + len(block) > _MAX_TOOL_OUTPUT:
                lines.append("\n[remaining history omitted due to tool output limit]")
                break
            lines.append(block)
            total_chars += len(block)
        return "\n".join(lines)

    @classmethod
    def _format_history_row(cls, row: dict[str, Any]) -> str:
        role, sender = cls._history_identity(row)
        flags = cls._history_flags(row)
        source = row.get("source") or role
        header = (
            f"\n[{row.get('turn_id')}] {row.get('created_at', '')} "
            f"[{sender}] [{source}]"
        )
        header += cls._history_header_suffix(row, flags)
        content = cls.sanitize_turn(str(row.get("content") or ""))
        return f"{header}\n{content}"

    @staticmethod
    def _history_identity(row: dict[str, Any]) -> tuple[str, str]:
        role = str(row.get("role") or "turn")
        sender = str(row.get("sender_display_name") or row.get("sender_id") or "")
        if not sender:
            sender = "bot" if role == "assistant" else role
        return role, sender

    @staticmethod
    def _history_flags(row: dict[str, Any]) -> list[str]:
        flags: list[str] = []
        if row.get("observed_only"):
            flags.append("observed")
        if trigger_kind := str(row.get("trigger_kind") or ""):
            flags.append(trigger_kind)
        return flags

    @staticmethod
    def _history_header_suffix(row: dict[str, Any], flags: list[str]) -> str:
        suffix = ""
        if flags:
            suffix += f" ({', '.join(flags)})"
        if message_ref := str(row.get("message_id") or ""):
            suffix += f" message_id={message_ref}"
        if reply_to := str(row.get("reply_to") or ""):
            suffix += f" reply_to={reply_to}"
        return suffix

    async def search(
        self,
        query: str,
        chat_address: str = "",
        role: str = "",
        limit: int = 20,
    ) -> str:
        """Search all conversation history and retain chat provenance."""
        _logger.debug(
            "tool.search_chat_history",
            query=query,
            chat_address=chat_address,
            role=role,
        )
        capped_limit = max(min(int(limit or 20), 50), 1)
        rows = await self._api.search_chat_history(
            query,
            chat_address=chat_address,
            role=role,
            limit=capped_limit,
        )
        if not rows:
            return "No matching conversation history found."
        name_map = await self.resolve_chat_names(
            [str(row.get("session_id", "")) for row in rows]
        )
        lines = [f"Found {len(rows)} conversation matches (newest first):"]
        for idx, row in enumerate(rows, start=1):
            role_value = str(row.get("role", "") or "")
            session_id = str(row.get("session_id", "") or "")
            created = str(row.get("created_at", "") or "")
            content = self.sanitize_turn(str(row.get("content", "") or ""))
            label = name_map.get(self.base_chat_key(session_id)) or session_id or "?"
            lines.append(
                f"\n{idx}. [{role_value or 'turn'}] [{label}] {created}\n{content}"
            )
        return "\n".join(lines)

    async def find_chat(self, name: str, platform: str = "") -> str:
        """Resolve a fuzzy chat name to typed chat addresses."""
        _logger.debug("tool.find_chat", name=name, platform=platform)
        rows = await self._api.search_chats(name, platform=platform)
        if not rows:
            return "No chats matched that name."
        lines = [f"Found {len(rows)} chats:"]
        for idx, row in enumerate(rows, start=1):
            chat_address = str(row.get("chat_address", "") or "")
            display_name = str(row.get("display_name", "") or "")
            plat = str(row.get("platform", "") or "")
            last_seen = str(row.get("last_seen_at", "") or "")
            lines.append(
                f"{idx}. [{chat_address}] {display_name} "
                f"({plat}, last seen {last_seen})"
            )
        return "\n".join(lines)

    async def resolve_chat_names(self, session_ids: list[str]) -> dict[str, str]:
        """Resolve base chat keys to friendly display names."""
        chat_keys = {self.base_chat_key(sid) for sid in session_ids if sid}
        chat_keys.discard("")
        if not chat_keys:
            return {}
        return await self._api.get_chat_names(list(chat_keys))

    @staticmethod
    def parse_datetime(value: str) -> datetime | None:
        """Parse an optional ISO-8601 value and normalize it to UTC."""
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{value!r} is not ISO-8601") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    @staticmethod
    def base_chat_key(session_id: str) -> str:
        """Strip an optional suffix from a derived session id."""
        if not session_id:
            return ""
        parts = session_id.split(":")
        if len(parts) >= 3:
            return ":".join(parts[:3])
        return session_id

    @staticmethod
    def sanitize_turn(content: str) -> str:
        """Strip media payload noise and bound one turn for model use."""
        if not content:
            return ""
        sanitized = _BASE64_DATA_URL_RE.sub("[media omitted]", content)
        sanitized = _LONG_BASE64_RE.sub("[data omitted]", sanitized)
        if len(sanitized) > _MAX_TURN_CHARS:
            sanitized = sanitized[:_MAX_TURN_CHARS].rstrip() + "..."
        return sanitized
