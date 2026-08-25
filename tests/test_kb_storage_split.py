"""Tests for the #26 KB storage split: per-collection files, cosine vec0
tables, and the split migration script."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from nahida_bot.agent.storage.manager import DocumentStoreManager
from nahida_bot.agent.storage.vector import SQLiteVecIndex, VectorRecord
from nahida_bot.agent.storage.sqlite_document_store import SQLiteDocumentStore
from nahida_bot.db.engine import DatabaseEngine
from scripts import migrate_kb_storage_split as migration


async def _table_names(engine: DatabaseEngine) -> set[str]:
    rows = await engine.fetch_all(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
    )
    return {str(row["name"]) for row in rows}


@pytest.mark.asyncio
async def test_split_layout_creates_per_collection_file(tmp_path: Path) -> None:
    main_engine = DatabaseEngine(tmp_path / "main.db")
    await main_engine.initialize()
    try:
        manager = DocumentStoreManager(main_engine, storage_dir=tmp_path / "kb")
        store = await manager.create("articles")
        await store.put("doc-1", "world tree and pure palace", title="Lore")

        kb_file = tmp_path / "kb" / "articles.db"
        assert kb_file.is_file()
        # Main db stays free of KB tables; the collection file has them.
        main_tables = await _table_names(main_engine)
        assert "articles_docs" not in main_tables
        kb_con = sqlite3.connect(str(kb_file))
        kb_tables = {
            row[0]
            for row in kb_con.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            )
        }
        kb_con.close()
        assert {"articles_docs", "articles_doc_fts", "articles_doc_embeddings"} <= (
            kb_tables
        )
        # Retrieval works through the split-layout store.
        hits = await store.search("world tree", limit=3)
        assert hits and hits[0].doc_id == "doc-1"
    finally:
        manager = DocumentStoreManager(main_engine)
        await manager.shutdown()
        await main_engine.close()


@pytest.mark.asyncio
async def test_delete_collection_removes_file(tmp_path: Path) -> None:
    main_engine = DatabaseEngine(tmp_path / "main.db")
    await main_engine.initialize()
    manager = DocumentStoreManager(main_engine, storage_dir=tmp_path / "kb")
    await manager.get_or_create("gone")
    kb_file = tmp_path / "kb" / "gone.db"
    assert kb_file.is_file()
    assert await manager.delete_collection("gone") is True
    assert not kb_file.exists()
    await manager.shutdown()
    await main_engine.close()


def test_engine_for_falls_back_to_shared_engine() -> None:
    class _StubEngine:
        pass

    stub = _StubEngine()
    manager = DocumentStoreManager(stub)  # type: ignore[arg-type]
    assert manager.engine_for("anything") is stub
    assert manager.collection_db_path("anything") is None


@pytest.mark.asyncio
async def test_vec_index_uses_cosine_metric(tmp_path: Path) -> None:
    engine = DatabaseEngine(tmp_path / "vec.db")
    await engine.initialize()
    try:
        index = SQLiteVecIndex(
            engine,
            dimensions=3,
            table_name="test_vec",
            map_table="test_vec_map",
        )
        await index.setup()
        rows = await engine.fetch_all(
            "SELECT sql FROM sqlite_master WHERE name = 'test_vec'"
        )
        assert "distance_metric=cosine" in str(rows[0]["sql"])

        await index.upsert(
            [
                VectorRecord(embedding_id="e1", item_id="a", embedding=[1.0, 0.0, 0.0]),
                VectorRecord(embedding_id="e2", item_id="b", embedding=[0.0, 1.0, 0.0]),
            ]
        )
        hits = await index.search([1.0, 0.0, 0.0], limit=1)
        assert hits and hits[0].item_id == "a"
    finally:
        await engine.close()


async def _seed_main_collection(main_db: Path, count: int) -> None:
    engine = DatabaseEngine(main_db)
    await engine.initialize()
    try:
        store = SQLiteDocumentStore(engine, collection="seeded")
        await store.setup()
        for number in range(count):
            doc_id = f"doc-{number}"
            await store.put(
                doc_id,
                f"content about dragons {number}",
                title=f"Title {number}",
                retrieval_text=f"Title {number}\ncontent about dragons {number}",
                node_type="passage",
            )
            await store.put_embedding(
                doc_id,
                [float(number), 0.5, 0.5],
                provider_id="test",
                model="test-model",
                content_hash=f"hash-{number}",
            )
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_migration_moves_collection_and_cleans_main(tmp_path: Path) -> None:
    main_db = tmp_path / "main.db"
    await _seed_main_collection(main_db, count=4)

    con = sqlite3.connect(str(main_db))
    discovered = migration.discover_collections(con)
    con.close()
    assert discovered == ["seeded"]

    stats = await migration.migrate_collection(
        main_db, "seeded", tmp_path / "kb", drop=True
    )
    assert stats["docs"] == 4
    assert stats["embeddings"] == 4
    assert stats["vec_indexed"] == 4
    assert stats["vec_skipped"] == 0

    kb_file = tmp_path / "kb" / "seeded.db"
    assert kb_file.is_file()
    kb_con = sqlite3.connect(str(kb_file))
    docs = kb_con.execute("SELECT COUNT(*) FROM seeded_docs").fetchone()[0]
    emb = kb_con.execute("SELECT COUNT(*) FROM seeded_doc_embeddings").fetchone()[0]
    vec_map = kb_con.execute("SELECT COUNT(*) FROM kb_seeded_vec_map").fetchone()[0]
    vec_sql = kb_con.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'kb_seeded_embedding_vec'"
    ).fetchone()
    kb_con.close()
    assert (docs, emb, vec_map) == (4, 4, 4)
    assert vec_sql and "distance_metric=cosine" in str(vec_sql[0])

    # Main db is cleaned of every KB table.
    con = sqlite3.connect(str(main_db))
    tables = {
        row[0]
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        )
    }
    con.close()
    assert not any(t.startswith(("seeded_", "kb_seeded_")) for t in tables)

    # FTS through the migrated store still finds the seeded content.
    engine = DatabaseEngine(kb_file)
    await engine.initialize()
    try:
        store = SQLiteDocumentStore(engine, collection="seeded")
        hits = await store.search("dragons", limit=3)
        assert hits
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_migration_no_drop_keeps_main_tables(tmp_path: Path) -> None:
    main_db = tmp_path / "main.db"
    await _seed_main_collection(main_db, count=2)
    await migration.migrate_collection(main_db, "seeded", tmp_path / "kb", drop=False)
    con = sqlite3.connect(str(main_db))
    tables = {
        row[0]
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        )
    }
    con.close()
    assert "seeded_docs" in tables


def test_load_pseudo_embedding_still_parses_split_json(tmp_path: Path) -> None:
    """Guard the JSON embedding contract the probe/migration rely on."""
    db = tmp_path / "emb.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE c_doc_embeddings (embedding_id TEXT PRIMARY KEY, doc_id TEXT,"
        " provider_id TEXT, model TEXT, dimensions INTEGER, content_hash TEXT,"
        " embedding_json TEXT, created_at TEXT)"
    )
    con.execute(
        "INSERT INTO c_doc_embeddings VALUES ('e', 'd', 'p', 'm', 2, 'h', ?, 't')",
        (json.dumps([1.0, 2.0]),),
    )
    con.commit()
    con.close()
    con = sqlite3.connect(str(db))
    raw = con.execute(
        "SELECT embedding_json FROM c_doc_embeddings WHERE doc_id='d'"
    ).fetchone()[0]
    con.close()
    assert json.loads(raw) == [1.0, 2.0]
