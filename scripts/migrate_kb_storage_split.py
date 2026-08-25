"""Migrate KB collections from the main db into per-collection files (#26).

For every ``{c}_docs`` + ``{c}_doc_fts`` + ``{c}_doc_embeddings`` triple found
in the main database, this script:

1. creates ``{storage_dir}/{c}.db`` (the split-layout home for the collection);
2. copies document rows verbatim (ids, hierarchy, timestamps) and rebuilds the
   FTS index with the CURRENT tokenizer (a retokenize pass for free);
3. copies the embedding JSON rows as-is — zero embedding API calls;
4. rebuilds the sqlite-vec index from the copied vectors with
   ``distance_metric=cosine`` (fixing the legacy L2 default);
5. optionally drops the KB tables from the main db and VACUUMs it.

Run it against a COPY of the database first (rehearsal with --no-drop), verify
with ``scripts/probe_kb_retrieval.py --kb-dir``, then run on production after
a backup. Usage:

    uv run python scripts/migrate_kb_storage_split.py --db data/nahida.db \
        --storage-dir data/kb [--dry-run] [--no-drop] [--no-vacuum]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nahida_bot.agent.storage.sqlite_document_store import (  # noqa: E402
    SQLiteDocumentStore,
)
from nahida_bot.agent.storage.tokenization import tokenize_for_fts  # noqa: E402
from nahida_bot.agent.storage.vector import SQLiteVecIndex, VectorRecord  # noqa: E402
from nahida_bot.db.engine import DatabaseEngine  # noqa: E402

_DOCS_COLUMNS = (
    "doc_id, title, content, status, metadata_json, created_at, updated_at, "
    "retrieval_text, path, source_id, chunk_index, parent_id, root_id, node_type"
)
_EMB_COLUMNS = (
    "embedding_id, doc_id, provider_id, model, dimensions, content_hash, "
    "embedding_json, created_at"
)
_COPY_BATCH = 500


def _load_sqlite_vec(con: sqlite3.Connection) -> bool:
    """Load sqlite-vec into a plain connection; False when unavailable."""
    try:
        import sqlite_vec  # type: ignore[import-not-found]
    except ImportError:
        return False
    con.enable_load_extension(True)
    try:
        sqlite_vec.load(con)
    finally:
        con.enable_load_extension(False)
    return True


def discover_collections(main_con: sqlite3.Connection) -> list[str]:
    """Find KB collections in the main db via their table triple."""
    tables = {
        row[0]
        for row in main_con.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        )
    }
    collections = []
    for table in tables:
        if not table.endswith("_docs"):
            continue
        collection = table[: -len("_docs")]
        if (
            collection
            and f"{collection}_doc_fts" in tables
            and f"{collection}_doc_embeddings" in tables
        ):
            collections.append(collection)
    return sorted(collections)


def _fts_indexes(title: str, content: str, retrieval_text: str) -> tuple[str, str]:
    """Mirror SQLiteDocumentStore.put's FTS index-text derivation."""
    index_text = retrieval_text or (f"{title}\n{content}" if title else content)
    title_index = tokenize_for_fts(title) if title else ""
    return title_index, tokenize_for_fts(index_text)


async def migrate_collection(
    main_db: Path,
    collection: str,
    storage_dir: Path,
    *,
    drop: bool,
    batch_size: int = _COPY_BATCH,
) -> dict[str, int]:
    """Move one collection into ``{storage_dir}/{collection}.db``."""
    storage_dir.mkdir(parents=True, exist_ok=True)
    kb_path = storage_dir / f"{collection}.db"

    kb_engine = DatabaseEngine(kb_path)
    await kb_engine.initialize()
    store = SQLiteDocumentStore(kb_engine, collection=collection)
    await store.setup()

    src = sqlite3.connect(str(main_db))
    src.row_factory = sqlite3.Row
    dst = sqlite3.connect(str(kb_path))
    stats: dict[str, int] = {}
    try:
        doc_rows = src.execute(
            f"SELECT {_DOCS_COLUMNS} FROM {collection}_docs"
        ).fetchall()
        dst.execute("BEGIN")
        dst.executemany(
            f"INSERT OR REPLACE INTO {collection}_docs ({_DOCS_COLUMNS}) "
            f"VALUES ({', '.join('?' * len(_DOCS_COLUMNS.split(',')))})",
            [tuple(row) for row in doc_rows],
        )
        fts_rows = []
        for row in doc_rows:
            title_index, content_index = _fts_indexes(
                row["title"], row["content"], row["retrieval_text"]
            )
            fts_rows.append((row["doc_id"], title_index, content_index))
        dst.executemany(
            f"INSERT INTO {collection}_doc_fts (doc_id, title_index, content_index) "
            "VALUES (?, ?, ?)",
            fts_rows,
        )
        emb_rows = src.execute(
            f"SELECT {_EMB_COLUMNS} FROM {collection}_doc_embeddings"
        ).fetchall()
        dst.executemany(
            f"INSERT OR REPLACE INTO {collection}_doc_embeddings ({_EMB_COLUMNS}) "
            f"VALUES ({', '.join('?' * len(_EMB_COLUMNS.split(',')))})",
            [tuple(row) for row in emb_rows],
        )
        dst.commit()
        stats["docs"] = len(doc_rows)
        stats["embeddings"] = len(emb_rows)

        # Rebuild the vec index from the copied JSON (zero API cost). The
        # dominant embedding width wins; mismatched rows are skipped + counted.
        widths = Counter()
        for row in emb_rows:
            vector = json.loads(row["embedding_json"] or "[]")
            if vector:
                widths[len(vector)] += 1
        dimensions = widths.most_common(1)[0][0] if widths else 0
        stats["vec_dimensions"] = dimensions
        index = SQLiteVecIndex(
            kb_engine,
            dimensions=max(dimensions, 1),
            table_name=f"kb_{collection}_embedding_vec",
            map_table=f"kb_{collection}_vec_map",
        )
        indexed = 0
        if dimensions:
            await index.setup()
            records: list[VectorRecord] = []
            for row in emb_rows:
                vector = json.loads(row["embedding_json"] or "[]")
                if len(vector) != dimensions or not vector:
                    continue
                records.append(
                    VectorRecord(
                        embedding_id=row["embedding_id"],
                        item_id=row["doc_id"],
                        embedding=vector,
                    )
                )
                if len(records) >= batch_size:
                    await index.upsert(records)
                    indexed += len(records)
                    records = []
            if records:
                await index.upsert(records)
                indexed += len(records)
        stats["vec_indexed"] = indexed
        stats["vec_skipped"] = stats["embeddings"] - indexed

        if drop:
            vec_loaded = _load_sqlite_vec(src)
            src.execute("BEGIN")
            for table in (
                f"{collection}_doc_fts",
                f"{collection}_doc_embeddings",
                f"{collection}_docs",
                f"kb_{collection}_vec_map",
            ):
                src.execute(f"DROP TABLE IF EXISTS {table}")
            if vec_loaded:
                src.execute(f"DROP TABLE IF EXISTS kb_{collection}_embedding_vec")
            else:
                leftover = src.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE name = ?",
                    (f"kb_{collection}_embedding_vec",),
                ).fetchone()[0]
                if leftover:
                    print(
                        f"[migrate] WARNING: sqlite-vec unavailable, vec0 table "
                        f"kb_{collection}_embedding_vec left in main db"
                    )
            src.commit()
        stats["dropped_from_main"] = 1 if drop else 0
    finally:
        src.close()
        dst.close()
        await kb_engine.close()
    return stats


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Path to the main database")
    parser.add_argument(
        "--storage-dir",
        default=str(ROOT / "data" / "kb"),
        help="Target directory for per-collection db files",
    )
    parser.add_argument(
        "--collections",
        default="",
        help="Comma-separated allowlist (default: all discovered)",
    )
    parser.add_argument("--dry-run", action="store_true", help="List only")
    parser.add_argument(
        "--no-drop", action="store_true", help="Copy but keep main-db tables"
    )
    parser.add_argument(
        "--no-vacuum", action="store_true", help="Skip the final main-db VACUUM"
    )
    args = parser.parse_args()

    main_db = Path(args.db).resolve()
    if not main_db.is_file():
        sys.exit(f"database not found: {main_db}")
    if not args.dry_run and not args.no_drop:
        import os

        if not os.access(main_db, os.W_OK):
            sys.exit(
                f"main db is read-only: {main_db}\n"
                "(snapshots pulled via scp are often mode 0444; chmod u+w the "
                "working copy, or pass --no-drop for a copy-only rehearsal)"
            )
    storage_dir = Path(args.storage_dir).resolve()
    allowlist = {c.strip() for c in args.collections.split(",") if c.strip()}

    con = sqlite3.connect(str(main_db))
    collections = discover_collections(con)
    con.close()
    if allowlist:
        collections = [c for c in collections if c in allowlist]
    if not collections:
        sys.exit("no KB collections found in the main db")

    print(f"[migrate] main db: {main_db}")
    print(f"[migrate] storage dir: {storage_dir}")
    print(f"[migrate] collections: {', '.join(collections)}")
    if args.dry_run:
        con = sqlite3.connect(f"file:{main_db.as_posix()}?mode=ro", uri=True)
        for collection in collections:
            docs = con.execute(f"SELECT COUNT(*) FROM {collection}_docs").fetchone()[0]
            emb = con.execute(
                f"SELECT COUNT(*) FROM {collection}_doc_embeddings"
            ).fetchone()[0]
            print(f"  {collection}: {docs} docs, {emb} embeddings")
        con.close()
        return

    totals: dict[str, int] = {}
    for collection in collections:
        stats = await migrate_collection(
            main_db, collection, storage_dir, drop=not args.no_drop
        )
        print(f"[migrate] {collection}: {stats}")
        for key, value in stats.items():
            totals[key] = totals.get(key, 0) + value

    if not args.no_drop and not args.no_vacuum:
        print("[migrate] VACUUM main db (reclaims freed space)...")
        con = sqlite3.connect(str(main_db))
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.execute("VACUUM")
        con.close()

    print(f"[migrate] done. totals: {totals}")
    print(
        "[migrate] verify with: uv run python scripts/probe_kb_retrieval.py "
        f"--db {main_db} --kb-dir {storage_dir}"
    )


if __name__ == "__main__":
    asyncio.run(main())
