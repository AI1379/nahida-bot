"""SQLite-backed memory store implementation."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from nahida_bot.agent.memory_models import ConversationTurn, MemoryRecord
from nahida_bot.agent.memory_store import MemoryStore
from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.db.repositories.sqlite_memory_repo import SQLiteMemoryRepository

_MIN_KEYWORD_LENGTH = 3
_KEYWORD_SPLIT = re.compile(r"[^\w]+", re.UNICODE)


def extract_keywords(text: str, *, min_length: int = _MIN_KEYWORD_LENGTH) -> list[str]:
    """Extract normalized keywords from text for indexing.

    Splits on non-word characters, lowercases, and filters short tokens.
    Preserves first-occurrence order with stable deduplication.
    """
    tokens = _KEYWORD_SPLIT.split(text.lower())
    seen: set[str] = set()
    result: list[str] = []
    for token in tokens:
        if len(token) >= min_length and token not in seen:
            seen.add(token)
            result.append(token)
    return result


def _row_to_record(
    row: dict[str, Any], *, keywords: list[str] | None = None
) -> MemoryRecord:
    """Convert a repository row dict into a MemoryRecord."""
    created_at_raw = row.get("created_at", "")
    if isinstance(created_at_raw, str) and created_at_raw:
        created_at = datetime.fromisoformat(created_at_raw)
    else:
        created_at = datetime.now(UTC)

    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        metadata = None

    return MemoryRecord(
        turn_id=row.get("id", 0),
        session_id=row.get("session_id", ""),
        turn=ConversationTurn(
            role=row.get("role", ""),
            content=row.get("content", ""),
            source=row.get("source", ""),
            metadata=metadata,
            created_at=created_at,
        ),
        keywords=list(keywords) if keywords else [],
    )


class SQLiteMemoryStore(MemoryStore):
    """SQLite-backed memory store using the memory repository."""

    def __init__(self, engine: DatabaseEngine) -> None:
        self._repo = SQLiteMemoryRepository(engine)

    async def ensure_session(
        self, session_id: str, workspace_id: str | None = None
    ) -> None:
        """Ensure a session exists before storing turns."""
        await self._repo.ensure_session(session_id, workspace_id)

    async def append_turn(self, session_id: str, turn: ConversationTurn) -> int:
        """Store a conversation turn with auto-extracted keywords."""
        keywords = extract_keywords(turn.content)
        return await self._repo.append_turn(
            session_id,
            role=turn.role,
            content=turn.content,
            source=turn.source,
            metadata=turn.metadata,
            keywords=keywords,
        )

    async def search(
        self, session_id: str, query: str, *, limit: int = 10
    ) -> list[MemoryRecord]:
        """Search by query keywords with multi-keyword OR aggregation.

        Falls back to time-ordered retrieval when no keyword matches.
        """
        query_keywords = extract_keywords(query)
        if query_keywords:
            rows = await self._repo.search_by_keywords(
                session_id, query_keywords, limit=limit
            )
            if rows:
                turn_ids = [row["id"] for row in rows]
                kw_map = await self._repo.get_keywords_for_turns(turn_ids)
                return [
                    _row_to_record(row, keywords=kw_map.get(row["id"], []))
                    for row in rows
                ]

        # Fallback: return recent turns when no keyword match.
        rows = await self._repo.get_recent_turns(session_id, limit=limit)
        turn_ids = [row["id"] for row in rows]
        kw_map = await self._repo.get_keywords_for_turns(turn_ids)
        return [_row_to_record(row, keywords=kw_map.get(row["id"], [])) for row in rows]

    async def get_recent(
        self, session_id: str, *, limit: int = 50
    ) -> list[MemoryRecord]:
        """Retrieve recent turns in chronological order with keywords."""
        rows = await self._repo.get_recent_turns(session_id, limit=limit)
        turn_ids = [row["id"] for row in rows]
        kw_map = await self._repo.get_keywords_for_turns(turn_ids)
        return [_row_to_record(row, keywords=kw_map.get(row["id"], [])) for row in rows]

    async def evict_before(self, cutoff: datetime) -> int:
        """Delete turns older than cutoff datetime."""
        return await self._repo.delete_turns_before(cutoff)
