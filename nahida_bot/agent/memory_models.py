"""Memory data models: ConversationTurn and MemoryRecord."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(slots=True, frozen=True)
class ConversationTurn:
    """A single turn in a conversation, used for memory persistence."""

    role: str
    content: str
    source: str = ""
    metadata: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(slots=True, frozen=True)
class MemoryRecord:
    """A stored memory entry retrieved from the memory store."""

    turn_id: int
    session_id: str
    turn: ConversationTurn
    keywords: list[str] = field(default_factory=list)
