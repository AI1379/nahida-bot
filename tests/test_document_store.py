from __future__ import annotations

import pytest

from nahida_bot.agent.storage.document_store import BackfillResult
from nahida_bot.agent.storage.embedding import HashEmbeddingProvider
from nahida_bot.agent.storage.sqlite_document_store import SQLiteDocumentStore
from nahida_bot.agent.storage.vector import SQLiteVecIndex
from nahida_bot.db.engine import DatabaseEngine


class _RecordingVectorIndex:
    def __init__(self) -> None:
        self.deleted_ids: list[list[str]] = []
        self.upserted_ids: list[list[str]] = []

    async def upsert(self, records) -> None:
        self.upserted_ids.append([record.embedding_id for record in records])

    async def delete(self, ids: list[str]) -> None:
        self.deleted_ids.append(list(ids))

    async def search(self, query_embedding: list[float], *, limit: int):
        return []


@pytest.mark.asyncio
async def test_put_embedding_replaces_prior_doc_embedding_and_cleans_vector_index() -> (
    None
):
    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    try:
        store = SQLiteDocumentStore(engine, collection="kb_docs")
        await store.setup()
        await store.put("doc_1", "Hello world", title="Greeting")
        vector_index = _RecordingVectorIndex()

        first_id = await store.put_embedding(
            "doc_1",
            [0.1, 0.2],
            provider_id="embedder",
            model="kb-embed",
            content_hash="hash-1",
            vector_index=vector_index,
        )
        await store.put("doc_1", "Updated hello world", title="Updated greeting")
        second_id = await store.put_embedding(
            "doc_1",
            [0.3, 0.4],
            provider_id="embedder",
            model="kb-embed",
            content_hash="hash-2",
            vector_index=vector_index,
        )

        assert first_id != second_id
        assert vector_index.deleted_ids == [[first_id]]
        assert vector_index.upserted_ids == [[first_id], [second_id]]
        assert (
            await store._repo.list_embedding_ids_for_doc(  # noqa: SLF001
                "doc_1",
                provider_id="embedder",
                model="kb-embed",
            )
        ) == [second_id]
        assert [result.doc_id for result in await store.search("world")] == ["doc_1"]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_sqlite_vec_drop_works_after_database_reconnect(tmp_path) -> None:
    db_path = tmp_path / "document-store.sqlite3"
    first_engine = DatabaseEngine(str(db_path))
    await first_engine.initialize()
    index = SQLiteVecIndex(
        first_engine,
        dimensions=2,
        table_name="kb_docs_embedding_vec",
        map_table="kb_docs_vec_map",
    )
    await index.setup()
    await first_engine.close()

    second_engine = DatabaseEngine(str(db_path))
    await second_engine.initialize()
    try:
        reloaded_index = SQLiteVecIndex(
            second_engine,
            dimensions=2,
            table_name="kb_docs_embedding_vec",
            map_table="kb_docs_vec_map",
        )
        await reloaded_index.drop()

        rows = await second_engine.fetch_all(
            "SELECT name FROM sqlite_master "
            "WHERE name IN ('kb_docs_embedding_vec', 'kb_docs_vec_map')"
        )
        assert rows == []
    finally:
        await second_engine.close()


@pytest.mark.asyncio
async def test_embed_documents_is_incremental_and_skips_unchanged() -> None:
    """Repeated backfill only embeds new or changed content, not the whole set.

    Regression test for the restart-recompute bug: ``embed_documents`` must skip
    documents whose current content already has a persisted vector for this
    provider+model, so a process restart does not re-embed the collection.
    """
    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    try:
        store = SQLiteDocumentStore(engine, collection="kb_docs")
        await store.setup()
        await store.put("doc_1", "Alice likes Python", title="Profile")
        await store.put("doc_2", "Bob prefers Rust", title="Profile")
        provider = HashEmbeddingProvider(dimensions=8)

        first = await store.embed_documents(provider, limit=10)
        assert first == BackfillResult(added=2, needed=2)

        # Nothing changed -> nothing re-embedded (no provider call; needed == 0).
        second = await store.embed_documents(provider, limit=10)
        assert second == BackfillResult(added=0, needed=0)

        # Editing one document changes its content hash -> only it re-embeds.
        await store.put("doc_1", "Alice now likes Go", title="Profile")
        third = await store.embed_documents(provider, limit=10)
        assert third == BackfillResult(added=1, needed=1)
    finally:
        await engine.close()
