"""Memory store abstract contract.

There are two contracts here:

- :class:`MemoryStore` — the turn/session persistence contract (the original
  ABC). Turn-only backends and test fakes implement just this.
- :class:`StructuredMemoryStore` — a :class:`typing.Protocol` describing the
  durable-items / embedding / hierarchy API (:meth:`search_items`,
  :meth:`append_item`, ...). ``SQLiteMemoryStore`` structurally satisfies it.

The structured protocol exists so high-level consumers (``MemoryService``,
future REST routes) can be typed against a real contract instead of
``getattr``-probing an ``Any`` store — the items API was never on the turn ABC,
which is why every consumer previously duck-typed around it. A turn-only backend
simply is not a ``StructuredMemoryStore`` and is never handed to the service.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from typing import Any, Protocol, runtime_checkable

from nahida_bot.agent.memory.models import (
    ConversationTurn,
    MemoryCandidate,
    MemoryItem,
    MemoryRecord,
    SessionSummary,
)
from nahida_bot.agent.memory.scope import (
    SCOPE_ID_GLOBAL,
    SCOPE_TYPE_GLOBAL,
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


@runtime_checkable
class StructuredMemoryStore(Protocol):
    """Structured durable-items API that ``SQLiteMemoryStore`` satisfies.

    This is the contract :class:`~nahida_bot.agent.memory.service.MemoryService`
    is typed against. Declared as a structural :class:`typing.Protocol` so the
    concrete store needs no inheritance change; ``@runtime_checkable`` allows a
    capability ``isinstance`` probe at a service boundary if ever needed.
    """

    async def search_items(
        self,
        query: str = "",
        *,
        scope_type: str | None = SCOPE_TYPE_GLOBAL,
        scope_id: str | None = SCOPE_ID_GLOBAL,
        limit: int = 10,
    ) -> list[MemoryItem]:
        """FTS5 BM25 search (or recent list when ``query`` is empty).

        ``None`` independently disables the corresponding scope filter. This
        is reserved for trusted admin surfaces; recall callers always pass an
        exact scope from the identity-aware cascade.
        """
        ...

    async def append_item(
        self,
        *,
        content: str,
        title: str = "",
        scope_type: str = SCOPE_TYPE_GLOBAL,
        scope_id: str = SCOPE_ID_GLOBAL,
        kind: str = "fact",
        source: str = "plugin",
        confidence: float = 1.0,
        importance: float = 0.5,
        sensitivity: str = "public",
        sensitivity_source: str = "default",
        evidence: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        item_id: str | None = None,
        parent_id: str = "",
        root_id: str = "",
        node_type: str = "leaf",
        path: str = "",
        source_id: str = "",
    ) -> str:
        """Store a durable structured memory item; return its id."""
        ...

    async def archive_item(self, item_id: str) -> bool:
        """Archive (soft-delete) a durable memory item."""
        ...

    async def get_items_by_ids(self, item_ids: list[str]) -> list[MemoryItem]:
        """Return active durable items by id in input order."""
        ...

    async def search_items_public(
        self,
        query: str = "",
        *,
        scope_type: str = SCOPE_TYPE_GLOBAL,
        scope_id: str = SCOPE_ID_GLOBAL,
        limit: int = 10,
    ) -> list[MemoryItem]:
        """Search active public items within one exact scope."""
        ...

    async def search_items_public_all_scopes(
        self,
        query: str = "",
        *,
        limit: int = 10,
    ) -> list[MemoryItem]:
        """Soft-scope cross-scope recall: public items across all scopes.

        The store enforces ``sensitivity='public'`` at the SQL layer.
        """
        ...

    async def list_public_items(
        self,
        *,
        scope_type: str = SCOPE_TYPE_GLOBAL,
        scope_id: str = SCOPE_ID_GLOBAL,
        limit: int = 40,
    ) -> list[MemoryItem]:
        """List active PUBLIC items for a scope (SQL-level sensitivity filter).

        Used by the Markdown projection so restricted items are excluded before
        the LIMIT and can't crowd public items out of the projection budget.
        """
        ...

    async def search_turns(
        self,
        query: str = "",
        *,
        chat_address: str = "",
        source: str = "",
        role: str = "",
        limit: int = 100,
    ) -> list[MemoryRecord]:
        """Cross-session raw-turn search for admin/debug views."""
        ...

    async def list_candidates(
        self,
        *,
        status: str | None = None,
        scope_type: str | None = SCOPE_TYPE_GLOBAL,
        scope_id: str | None = SCOPE_ID_GLOBAL,
        limit: int = 20,
    ) -> list[MemoryCandidate]:
        """List consolidation candidates (audit ledger)."""
        ...


def resolve_public_search(store: Any, *, public_only: bool = False) -> Any | None:
    """Return the best available ``(query, *, scope_type, scope_id, limit)``
    search callable for a store.

    When *public_only* is true the function prefers the store's
    ``search_items_public`` so the sensitivity predicate can be pushed to the
    SQL/index layer.  It falls back to plain ``search_items`` when the store
    has not yet implemented the public variant; the caller is then expected to
    post-filter by ``sensitivity == "public"`` itself.

    Returns ``None`` when no search method is available at all.
    """
    if public_only:
        candidate = getattr(store, "search_items_public", None)
        if callable(candidate):
            return candidate
    search = getattr(store, "search_items", None)
    if callable(search):
        return search
    return None
