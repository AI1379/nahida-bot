"""Memory store abstract contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from nahida_bot.agent.memory_models import ConversationTurn, MemoryRecord


class MemoryStore(ABC):
    """Abstract base class for memory persistence backends."""

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
