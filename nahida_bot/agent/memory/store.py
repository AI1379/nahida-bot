"""Memory store abstract contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from typing import Any

from nahida_bot.agent.memory.models import (
    ConversationTurn,
    MemoryRecord,
    SessionSummary,
)


class MemoryStore(ABC):
    """Abstract base class for memory persistence backends."""

    @abstractmethod
    async def ensure_session(
        self, session_id: str, workspace_id: str | None = None
    ) -> None:
        """Ensure session metadata exists before appending turns."""
        raise NotImplementedError

    @abstractmethod
    async def append_turn(self, session_id: str, turn: ConversationTurn) -> int:
        """Store a conversation turn and return its ID."""
        raise NotImplementedError

    @abstractmethod
    async def search(
        self, session_id: str, query: str, *, limit: int = 10
    ) -> list[MemoryRecord]:
        """Search memories by query string."""
        raise NotImplementedError

    @abstractmethod
    async def get_recent(
        self, session_id: str, *, limit: int = 50
    ) -> list[MemoryRecord]:
        """Retrieve recent conversation turns for a session."""
        raise NotImplementedError

    @abstractmethod
    async def evict_before(self, cutoff: datetime) -> int:
        """Delete memories older than cutoff. Returns deleted count."""
        raise NotImplementedError

    @abstractmethod
    async def clear_session(self, session_id: str) -> int:
        """Delete all turns and keywords for a session. Returns deleted turn count."""
        raise NotImplementedError

    @abstractmethod
    async def list_sessions(self, *, limit: int = 50) -> list[SessionSummary]:
        """List sessions with metadata and turn counts."""
        raise NotImplementedError

    @abstractmethod
    async def get_session_meta(self, session_id: str) -> dict[str, Any]:
        """Get session metadata. Returns empty dict if not found."""
        raise NotImplementedError

    @abstractmethod
    async def update_session_meta(
        self, session_id: str, updates: dict[str, Any]
    ) -> None:
        """Merge updates into session metadata (upsert)."""
        raise NotImplementedError

    @abstractmethod
    async def persist_active_session(self, chat_key: str, session_id: str) -> None:
        """Persist the active session override for a chat key."""
        raise NotImplementedError

    @abstractmethod
    async def load_active_sessions(self) -> dict[str, str]:
        """Load all persisted session overrides as {chat_key: session_id}."""
        raise NotImplementedError
