from __future__ import annotations

import pytest

from nahida_bot.agent.storage.sqlite_document_store import SQLiteDocumentStore
from nahida_bot.db.engine import DatabaseEngine
from scripts import migrate_kb_vector_backend as migration


async def _seed_collection(engine: DatabaseEngine, count: int) -> None:
    store = SQLiteDocumentStore(engine, collection="articles")
    await store.setup()
    for number in range(count):
        doc_id = f"doc-{number}"
        embedding = [float(number), 0.25, 0.5]
        await store.put(doc_id, f"Document {number}")
        await store.put_embedding(
            doc_id,
            embedding,
            provider_id="test",
            model="test-model",
            content_hash=f"hash-{number}",
        )


@pytest.mark.asyncio
async def test_migration_reads_and_indexes_embeddings_in_batches(monkeypatch) -> None:
    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    try:
        await _seed_collection(engine, 5)
        upsert_batch_sizes: list[int] = []
        original_upsert = migration.SQLiteVecIndex.upsert

        async def record_upsert(index, records) -> None:
            upsert_batch_sizes.append(len(records))
            await original_upsert(index, records)

        monkeypatch.setattr(migration.SQLiteVecIndex, "upsert", record_upsert)

        found, indexed, skipped = await migration.migrate_collection(
            engine,
            "articles",
            dry_run=False,
            batch_size=2,
        )

        assert (found, indexed, skipped) == (5, 5, 0)
        assert upsert_batch_sizes == [2, 2, 1]
        rows = await engine.fetch_all("SELECT embedding_id FROM kb_articles_vec_map")
        assert len(rows) == 5
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_migration_dry_run_counts_without_creating_an_index(monkeypatch) -> None:
    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    try:
        await _seed_collection(engine, 2)
        await engine.execute(
            "UPDATE articles_doc_embeddings SET embedding_json = ?",
            ("not valid json",),
        )
        await engine.db.commit()

        def fail_if_an_index_is_created(*args, **kwargs) -> None:
            raise AssertionError("dry-run must not create a vector index")

        monkeypatch.setattr(migration, "SQLiteVecIndex", fail_if_an_index_is_created)

        found, indexed, skipped = await migration.migrate_collection(
            engine,
            "articles",
            dry_run=True,
            batch_size=1,
        )

        assert (found, indexed, skipped) == (2, 0, 0)
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_vector_index_upsert_reuses_existing_rows_without_per_record_lookup(
    monkeypatch,
) -> None:
    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    try:
        index = migration.SQLiteVecIndex(
            engine,
            dimensions=3,
            table_name="kb_articles_embedding_vec",
            map_table="kb_articles_vec_map",
        )
        await index.setup()

        async def fail_on_single_row_lookup(*args, **kwargs) -> None:
            raise AssertionError("upsert must use batched map lookups")

        monkeypatch.setattr(engine, "fetch_one", fail_on_single_row_lookup)
        await index.upsert(
            [
                migration.VectorRecord("emb-1", "doc-1", [0.0, 0.1, 0.2]),
                migration.VectorRecord("emb-2", "doc-2", [0.3, 0.4, 0.5]),
            ]
        )
        await index.upsert(
            [
                migration.VectorRecord("emb-1", "doc-1", [0.6, 0.7, 0.8]),
                migration.VectorRecord("emb-2", "doc-2", [0.9, 1.0, 1.1]),
            ]
        )

        rows = await engine.fetch_all("SELECT embedding_id FROM kb_articles_vec_map")
        hits = await index.search([0.6, 0.7, 0.8], limit=1)

        assert sorted(str(row["embedding_id"]) for row in rows) == ["emb-1", "emb-2"]
        assert [hit.item_id for hit in hits] == ["doc-1"]
    finally:
        await engine.close()
