"""DocumentStoreManager — collection registry and lifecycle management."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from nahida_bot.agent.storage.document_store import DocumentStore
from nahida_bot.agent.storage.sqlite_document_store import SQLiteDocumentStore

if TYPE_CHECKING:
    from nahida_bot.db.engine import DatabaseEngine

logger = structlog.get_logger(__name__)


class DocumentStoreManager:
    """Creates and manages ``DocumentStore`` instances for named collections.

    Each collection maps to a physically isolated set of SQLite tables. The
    manager acts as a registry so that:

    * Collections are not accidentally created twice.
    * Plugins can discover existing collections.
    * Cleanup (dropping tables) is centralized.

    Usage::

        manager = DocumentStoreManager(engine)
        store = await manager.create("python_docs")
        await store.put("chunk_1", content="...", title="AsyncIO")

        # Later, from another module:
        store = manager.get("python_docs")
        results = await store.search("how to use asyncio")
    """

    def __init__(self, engine: DatabaseEngine) -> None:
        self._engine = engine
        self._stores: dict[str, DocumentStore] = {}

    async def create(self, name: str) -> DocumentStore:
        """Create and register a new collection.

        Raises ``ValueError`` if a collection with this name already exists.
        """
        if name in self._stores:
            raise ValueError(f"Collection '{name}' already exists")
        store = SQLiteDocumentStore(self._engine, collection=name)
        await store.setup()
        self._stores[name] = store
        logger.info("document_store_manager.created", collection=name)
        return store

    async def get_or_create(self, name: str) -> DocumentStore:
        """Get an existing collection, or create it if it does not exist."""
        if name not in self._stores:
            store = SQLiteDocumentStore(self._engine, collection=name)
            await store.setup()
            self._stores[name] = store
            logger.debug("document_store_manager.auto_created", collection=name)
        return self._stores[name]

    def get(self, name: str) -> DocumentStore | None:
        """Look up an existing collection.  Returns ``None`` if not found."""
        return self._stores.get(name)

    def list_collections(self) -> list[str]:
        """Return the names of all registered collections."""
        return list(self._stores.keys())

    async def delete_collection(self, name: str) -> bool:
        """Drop all tables for a collection and unregister it.

        Returns ``True`` if the collection existed and was deleted.
        """
        store = self._stores.pop(name, None)
        if store is None:
            return False
        # Access the repository directly to drop tables.
        if isinstance(store, SQLiteDocumentStore):
            await store._repo.drop_tables()  # noqa: SLF001
        logger.info("document_store_manager.deleted", collection=name)
        return True

    async def shutdown(self) -> None:
        """Clear all registered stores (does not drop tables)."""
        self._stores.clear()
        logger.debug("document_store_manager.shutdown")
