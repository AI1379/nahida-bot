"""DocumentStoreManager — collection registry and lifecycle management."""

from __future__ import annotations

from pathlib import Path

import structlog

from nahida_bot.agent.storage.document_store import DocumentStore
from nahida_bot.agent.storage.sqlite_document_store import SQLiteDocumentStore
from nahida_bot.db.engine import DatabaseEngine

logger = structlog.get_logger(__name__)


class DocumentStoreManager:
    """Creates and manages ``DocumentStore`` instances for named collections.

    Each collection maps to a physically isolated set of SQLite tables. The
    manager acts as a registry so that:

    * Collections are not accidentally created twice.
    * Plugins can discover existing collections.
    * Cleanup (dropping tables) is centralized.

    Storage layouts (issue #26, kb-direction.md §5):

    * **Legacy (default)** — every collection's tables live in the shared
      engine's database (``{collection}_docs`` and friends in the main bot db).
    * **Split** — pass ``storage_dir`` and each collection gets its own SQLite
      file at ``{storage_dir}/{collection}.db`` holding its docs, FTS,
      embedding JSON, and vec0 index. A collection's lifecycle is the file's
      lifecycle (delete drops the file); the main db keeps only bot-core data.
      All access continues through this manager, so callers stay layout-blind.

    Usage::

        manager = DocumentStoreManager(engine)
        store = await manager.create("python_docs")
        await store.put("chunk_1", content="...", title="AsyncIO")

        # Later, from another module:
        store = manager.get("python_docs")
        results = await store.search("how to use asyncio")
    """

    def __init__(
        self,
        engine: DatabaseEngine,
        *,
        storage_dir: str | Path | None = None,
    ) -> None:
        self._engine = engine
        self._storage_dir = Path(storage_dir) if storage_dir else None
        self._stores: dict[str, DocumentStore] = {}
        self._collection_engines: dict[str, DatabaseEngine] = {}
        self._collection_paths: dict[str, Path] = {}

    @property
    def engine(self) -> DatabaseEngine:
        """Return the shared database engine for collection-level helpers."""
        return self._engine

    @property
    def storage_dir(self) -> Path | None:
        """Per-collection database directory, or None in legacy layout."""
        return self._storage_dir

    def engine_for(self, name: str) -> DatabaseEngine:
        """Return the engine hosting one collection's tables.

        In split mode this is the collection's own file engine once the
        collection has been created; before that (and always in legacy mode)
        it falls back to the shared engine.
        """
        return self._collection_engines.get(name, self._engine)

    def collection_db_path(self, name: str) -> Path | None:
        """Return the collection's database file path in split mode."""
        if self._storage_dir is None:
            return None
        return self._storage_dir / f"{name}.db"

    async def _engine_for_create(self, name: str) -> DatabaseEngine:
        """Return (creating if needed) the engine for a new collection."""
        storage_dir = self._storage_dir
        if storage_dir is None:
            return self._engine
        existing = self._collection_engines.get(name)
        if existing is not None:
            return existing
        storage_dir.mkdir(parents=True, exist_ok=True)
        db_path = storage_dir / f"{name}.db"
        collection_engine = DatabaseEngine(db_path)
        await collection_engine.initialize()
        self._collection_engines[name] = collection_engine
        self._collection_paths[name] = db_path
        return collection_engine

    async def create(self, name: str) -> DocumentStore:
        """Create and register a new collection.

        Raises ``ValueError`` if a collection with this name already exists.
        """
        if name in self._stores:
            raise ValueError(f"Collection '{name}' already exists")
        engine = await self._engine_for_create(name)
        store = SQLiteDocumentStore(engine, collection=name)
        await store.setup()
        self._stores[name] = store
        logger.info(
            "document_store_manager.created",
            collection=name,
            layout="split" if self._storage_dir is not None else "legacy",
        )
        return store

    async def get_or_create(self, name: str) -> DocumentStore:
        """Get an existing collection, or create it if it doesn't exist."""
        if name not in self._stores:
            engine = await self._engine_for_create(name)
            store = SQLiteDocumentStore(engine, collection=name)
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

        In split mode this also closes and removes the collection's database
        file (with its WAL/SHM siblings) — the file IS the collection.

        Returns ``True`` if the collection existed and was deleted.
        """
        store = self._stores.pop(name, None)
        if store is None:
            return False
        # Access the repository directly to drop tables.
        if isinstance(store, SQLiteDocumentStore):
            await store._repo.drop_tables()  # noqa: SLF001
        logger.info("document_store_manager.deleted", collection=name)

        engine = self._collection_engines.pop(name, None)
        db_path = self._collection_paths.pop(name, None)
        if engine is not None:
            await engine.close()
            if db_path is not None:
                for suffix in ("", "-wal", "-shm"):
                    Path(str(db_path) + suffix).unlink(missing_ok=True)
        return True

    async def shutdown(self) -> None:
        """Clear all registered stores and close per-collection engines."""
        self._stores.clear()
        for engine in self._collection_engines.values():
            try:
                await engine.close()
            except Exception:
                logger.debug("document_store_manager.engine_close_failed")
        self._collection_engines.clear()
        logger.debug("document_store_manager.shutdown")
